"""Private controller tests use fictional Docker results, never a real daemon."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.service import Configuration, InventoryServer, InventoryUnavailable, inventory


TOKEN = "fictional-controller-token-1234567890"
ENV = {"HOTEL_SPEECH_CONTROLLER_TOKEN": TOKEN, "HOTEL_SPEECH_CONTROLLER_HOST": "fixture-host",
       "HOTEL_SPEECH_CONTROLLER_CONTAINERS": '["fixture-asr", "fixture-tts"]'}


def docker_output():
    return b"\n".join(json.dumps({"container_name": "/" + name, "id": "a" * 64,
        "state": "running", "health": "healthy", "image_id": "sha256:" + "b" * 64,
        "configured_image": "example.test/speech@sha256:" + "c" * 64,
        "nano_cpus": 500000000, "memory_bytes": 536870912}).encode()
        for name in ("fixture-asr", "fixture-tts"))


@contextmanager
def running_server():
    with InventoryServer(Configuration.from_env(ENV), port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)


def request(server, method="GET", path="/v1/inventory", token=TOKEN):
    connection = HTTPConnection(*server.server_address, timeout=3)
    try:
        headers = {"Authorization": "Bearer " + token} if token is not None else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class ControllerTests(unittest.TestCase):
    def test_configuration_rejects_unbounded_or_command_inputs(self):
        config = Configuration.from_env(ENV)
        self.assertEqual(config.port, 8092)
        invalid = [
            {"HOTEL_SPEECH_CONTROLLER_TOKEN": "short"},
            {"HOTEL_SPEECH_CONTROLLER_TOKEN": "a" * 31 + " "},
            {"HOTEL_SPEECH_CONTROLLER_TOKEN": "a" * 31 + "é"},
            {"HOTEL_SPEECH_CONTROLLER_HOST": "https://host"},
            {"HOTEL_SPEECH_CONTROLLER_PORT": "0"},
        ]
        invalid.extend({"HOTEL_SPEECH_CONTROLLER_CONTAINERS": value} for value in (
            "[]", "null", "bad json", '["x", "x"]', '["--help"]', '["x; command"]',
            json.dumps(["x" + str(i) for i in range(9)]), '[42]',
        ))
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError) as caught:
                Configuration.from_env(ENV | change)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_inventory_projects_allowlist_and_fixed_command(self):
        with patch("speech_controller.service.subprocess.run", return_value=subprocess.CompletedProcess([], 0, docker_output())) as run:
            result = inventory(Configuration.from_env(ENV))
        self.assertEqual(result["host"], "fixture-host")
        self.assertEqual(len(result["deployments"]), 2)
        self.assertEqual(result["deployments"][0]["cpu_limit"], 0.5)
        self.assertEqual(result["deployments"][0]["memory_mib"], 512)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], ["docker", "inspect", "--type", "container", "--format"])
        self.assertEqual(argv[-2:], ["fixture-asr", "fixture-tts"])
        self.assertNotIn("{{json .}}", argv[5])
        self.assertNotIn("Env", argv[5])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_failure_and_malformed_inventory_fail_closed(self):
        for error in (FileNotFoundError("secret"), subprocess.TimeoutExpired("secret", 5),
                      subprocess.CalledProcessError(1, "secret", stderr="secret")):
            with self.subTest(error=error), patch("speech_controller.service.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(InventoryUnavailable, "^Inventory unavailable.$"):
                    inventory(Configuration.from_env(ENV))
        invalid = [b"secret-invalid-json", b"[]", docker_output().splitlines()[0], b"x" * 65537]
        records = [json.loads(line) for line in docker_output().splitlines()]
        for changes in ({"id": ""}, {"image_id": "bad"}, {"state": "unknown"}, {"health": "bad"},
                        {"configured_image": ""}, {"container_name": "/other"}, {"nano_cpus": True},
                        {"memory_bytes": -1}, {"Env": ["secret"]}):
            invalid.append(b"\n".join(json.dumps(record | (changes if i == 0 else {})).encode()
                                       for i, record in enumerate(records)))
        for output in invalid:
            with self.subTest(output=output[:50]), patch("speech_controller.service.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, output)):
                with self.assertRaises(InventoryUnavailable):
                    inventory(Configuration.from_env(ENV))

    def test_http_auth_methods_readonly_response_and_error_redaction(self):
        with running_server() as server, patch("speech_controller.service.inventory") as read:
            for method, path, token, status in (
                ("GET", "/v1/inventory", None, 401), ("GET", "/anything", "wrong", 401),
                ("POST", "/v1/inventory", "wrong", 401), ("POST", "/v1/inventory", TOKEN, 405),
                ("GET", "/v1/inventory?container=other", TOKEN, 404),
            ):
                self.assertEqual(request(server, method, path, token)[0], status)
            read.assert_not_called()
            read.return_value = {"host": "fixture-host", "deployments": []}
            status, headers, body = request(server)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(int(headers["Content-Length"]), len(body))
            read.side_effect = InventoryUnavailable("secret docker output")
            status, _, body = request(server)
            self.assertEqual(status, 503)
            self.assertNotIn(b"secret", body)

    def test_concurrent_inventory_busy_and_recovery(self):
        entered, release = Event(), Event()

        def blocking_inventory(config):
            entered.set()
            release.wait(2)
            return {"deployments": []}

        with running_server() as server, patch("speech_controller.service.inventory", side_effect=blocking_inventory):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(request, server)
                self.assertTrue(entered.wait(1))
                try:
                    status, headers, _ = request(server)
                    self.assertEqual(status, 429)
                    self.assertEqual(headers["Retry-After"], "1")
                finally:
                    release.set()
                self.assertEqual(first.result()[0], 200)
            self.assertEqual(request(server)[0], 200)


if __name__ == "__main__":
    unittest.main()
