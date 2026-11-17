"""Real loopback HTTP dispatch over durable stores and fictional Docker effects."""
from dataclasses import asdict
from http.client import HTTPConnection, HTTPResponse, RemoteDisconnected
from pathlib import Path
import json
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.dispatch import ControlConfiguration, ControlServer
from speech_controller.lifecycle import SwitchRequest
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from test_controller_routed_executor import Controls, Crash, DEPLOYMENTS, EN, ROUTES, TARGETS


TOKEN = "fictional-control-token-" + "x" * 32
SWITCH = dict(request_id="switch-en", expected_revision=0, slot=["en", "stt"], target="new")


class DispatchTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        initialize_routing(root / "traffic.sqlite3", TARGETS, ROUTES)
        self.gate = RoutedTrafficGate(root / "traffic.sqlite3", TARGETS)
        self.controls = Controls()
        self.backend = DockerLifecycleBackend(self.controls, DEPLOYMENTS,
            admission=lambda *args: True, traffic=self.gate, readiness=lambda *args: True,
            reconciliation=lambda *args: True)
        self.executor = RoutedLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, root / "journal.sqlite3")
        self.server = ControlServer(ControlConfiguration(TOKEN, "private-admin-service"), self.executor, port=0)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method="GET", path="/v1/status", value=None, headers=None, body=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            payload = json.dumps(value).encode() if value is not None else body
            connection.request(method, path, payload, headers if headers is not None else {
                "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None, dict(response.getheaders())
        finally:
            connection.close()

    def raw(self, headers, body=b"", method="POST", path="/v1/switch"):
        with socket.create_connection(self.server.server_address, timeout=5) as connection:
            head = f"{method} {path} HTTP/1.0\r\n" + "\r\n".join(headers) + "\r\n\r\n"
            connection.sendall(head.encode() + body)
            response = HTTPResponse(connection)
            response.begin()
            response.read()
            return response.status

    def test_authentication_and_status_are_private_and_read_only(self):
        for headers in ({}, {"Authorization": "Bearer incorrect"}):
            self.assertEqual(self.request("POST", "/v1/switch", SWITCH, headers)[0], 401)
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], body["observed"])
        self.assertFalse(body["blocked"])
        self.assertEqual(body["pending_recoveries"], [])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.controls.effects, [])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertNotIn(TOKEN, repr(self.server.config))

    def test_switch_replay_and_conflict_preserve_effects(self):
        status, first, _ = self.request("POST", "/v1/switch", SWITCH)
        self.assertEqual(status, 200)
        self.assertEqual(first["status"], "succeeded")
        effects = list(self.controls.effects)
        self.assertEqual(self.request("POST", "/v1/switch", SWITCH)[1], first)
        conflict = self.request("POST", "/v1/switch", SWITCH | {"target": "old"})[1]
        self.assertEqual(conflict["reason"], "request_id_conflict")
        self.assertEqual(self.controls.effects, effects)

    def test_recovery_uses_configured_actor_and_observed_route(self):
        with patch.object(self.executor, "_checkpoint", side_effect=Crash):
            with self.assertRaises(Crash):
                self.executor.execute(SwitchRequest("interrupted", 0, EN, "new"))
        status = self.request()[1]
        self.assertNotEqual(status["observed"], status["state"])
        recovery = dict(recovery_id="recover-one", expected_revision=0,
                        observed=status["observed"], reason="Verified fictional workers")
        self.assertEqual(self.request("POST", "/v1/recover", recovery | {"actor": "impersonation"})[0], 400)
        answer = self.request("POST", "/v1/recover", recovery)
        self.assertEqual(answer[0], 200)
        self.assertEqual(answer[1]["status"], "reconciled")
        audit = self.executor.reconciliation_history()[0]
        self.assertEqual(audit["request"]["actor"], "private-admin-service")
        self.assertEqual(self.request("POST", "/v1/recover", recovery)[1], answer[1])

    def test_invalid_configuration_cannot_start_service(self):
        for token, actor, port in (("short", "operator", 8093), (TOKEN + "\n", "operator", 8093),
                (TOKEN + "ą", "operator", 8093), (TOKEN, " ", 8093), (TOKEN, "x" * 121, 8093),
                (TOKEN, "bad\x7f", 8093), (TOKEN, "operator", True), (TOKEN, "operator", 65536)):
            with self.subTest(actor=actor, port=port), self.assertRaises(ValueError):
                ControlConfiguration(token, actor, port)
        with self.assertRaises(ValueError):
            ControlServer(ControlConfiguration(TOKEN, "operator"), object(), port=0)

    def test_invalid_json_and_command_fields_never_reach_executor(self):
        invalid = [[], None, SWITCH | {"extra": "ignored?"}, SWITCH | {"slot": "en,stt"},
                   SWITCH | {"slot": ["en", "other"]}, SWITCH | {"slot": [["en"], "stt"]},
                   SWITCH | {"expected_revision": True}, SWITCH | {"expected_revision": -1},
                   SWITCH | {"expected_revision": 2**63}, SWITCH | {"target": "http://unapproved"}]
        with patch.object(self.executor, "execute", side_effect=AssertionError("invalid input executed")) as execute:
            for value in invalid:
                with self.subTest(value=value):
                    self.assertEqual(self.request("POST", "/v1/switch", body=json.dumps(value).encode())[0], 400)
            for body in (b'{"request_id":"a","request_id":"b"}', b'{"value": NaN}',
                         b"\xff", b"{" * 3000, b"broken", b"[] []"):
                self.assertEqual(self.request("POST", "/v1/switch", body=body)[0], 400)
            execute.assert_not_called()
        self.assertEqual(self.controls.effects, [])

    def test_recovery_rejects_duplicate_incompatible_or_missing_slots(self):
        routes = json.loads(json.dumps(asdict(self.gate.state)))
        valid = dict(recovery_id="recover", expected_revision=0, observed=routes, reason="Verified")
        invalid = [valid | {"reason": ""}, valid | {"reason": 1}, valid | {"reason": "x\n"},
                   valid | {"actor": "caller"}, valid | {"expected_revision": False}]
        duplicate = json.loads(json.dumps(routes))
        duplicate["routes"][1] = duplicate["routes"][0]
        incompatible = json.loads(json.dumps(routes))
        incompatible["routes"][0][1] = "tts"
        invalid += [valid | {"observed": value} for value in (
            duplicate, incompatible, routes | {"revision": True}, routes | {"routes": []},
            routes | {"extra": 1}, routes | {"routes": "bad"})]
        with patch.object(self.executor, "recover", side_effect=AssertionError("invalid recovery executed")) as recover:
            for value in invalid:
                with self.subTest(value=value):
                    self.assertEqual(self.request("POST", "/v1/recover", value)[0], 400)
            recover.assert_not_called()

    def test_ambiguous_framing_and_duplicate_auth_are_rejected_before_execution(self):
        auth = "Authorization: Bearer " + TOKEN
        content_type = "Content-Type: application/json"
        invalid = [[], ["Content-Length: 0"], ["Content-Length: -1"], ["Content-Length: +2"],
                   ["Content-Length: 4097"], ["Content-Length: 9999999999"], ["Content-Length: 2, 2"],
                   ["Content-Length: 2", "Content-Length: 2"],
                   ["Content-Length: 2", "Transfer-Encoding: chunked"],
                   ["Content-Length: 2", "Content-Type: application/json"]]
        for framing in invalid:
            with self.subTest(framing=framing):
                self.assertEqual(self.raw([auth, content_type, *framing], b"{}"), 400)
        self.assertEqual(self.raw([auth, "Content-Length: 2"], b"{}"), 400)
        self.assertEqual(self.raw([auth, "Content-Type: text/plain", "Content-Length: 2"], b"{}"), 400)
        # A missing body cannot delay an unauthorized response.
        self.assertEqual(self.raw([auth, auth, content_type, "Content-Length: 4096"]), 401)
        self.assertEqual(self.raw([content_type, "Content-Length: 4096"]), 401)
        self.assertEqual(self.controls.effects, [])

    def test_paths_methods_and_backend_errors_do_not_leak_details(self):
        for method, path, expected in (("POST", "/v1/status", 405), ("GET", "/v1/switch", 405),
                ("GET", "/v1/status?secret", 404), ("POST", "/v1/inventory", 404),
                ("DELETE", "/v1/recover", 405)):
            self.assertEqual(self.request(method, path)[0], expected)
        for error in (RuntimeError(TOKEN), ValueError(TOKEN)):
            with patch.object(self.executor, "execute", side_effect=error):
                status, body, _ = self.request("POST", "/v1/switch", SWITCH)
                self.assertEqual(status, 503)
                self.assertNotIn(TOKEN, json.dumps(body))
        with patch.object(self.executor, "recovery_status", side_effect=ValueError(TOKEN)):
            self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.request()[0], 200)

    def test_concurrent_requests_are_busy_without_queuing_effects(self):
        entered, release = Event(), Event()
        original = self.executor.execute
        answers = []
        def blocked(request):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release missed")
            return original(request)
        with patch.object(self.executor, "execute", side_effect=blocked):
            thread = Thread(target=lambda: answers.append(self.request("POST", "/v1/switch", SWITCH)))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                status, _, headers = self.request()
                self.assertEqual(status, 429)
                self.assertEqual(headers["Retry-After"], "1")
            finally:
                release.set()
                thread.join(5)
        self.assertEqual(answers[0][0], 200)
        self.assertEqual(self.controls.effects, [("start", "new")])

    def test_body_deadline_bounds_a_trickling_client_and_releases_dispatch(self):
        finished = Event()
        with patch("speech_controller.dispatch.BODY_TIMEOUT", 0.3):
            with socket.create_connection(self.server.server_address, timeout=3) as connection:
                head = ("POST /v1/switch HTTP/1.0\r\nAuthorization: Bearer " + TOKEN
                        + "\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
                started = monotonic()
                connection.sendall(head.encode())
                def trickle():
                    while not finished.wait(0.05):
                        try:
                            connection.sendall(b" ")
                        except OSError:
                            return
                thread = Thread(target=trickle)
                thread.start()
                try:
                    response = HTTPResponse(connection)
                    response.begin()
                    self.assertEqual(response.status, 400)
                    response.read()
                    self.assertLess(monotonic() - started, 1.5)
                finally:
                    finished.set()
                    thread.join(2)
        self.assertEqual(self.request("POST", "/v1/switch", SWITCH)[1]["status"], "succeeded")

    def test_worker_limit_rejects_connections_without_leaking_slots(self):
        for _ in range(8):
            self.assertTrue(self.server.workers.acquire(blocking=False))
        try:
            with self.assertRaises((RemoteDisconnected, ConnectionError)):
                self.request()
        finally:
            for _ in range(8):
                self.server.workers.release()
        self.assertEqual(self.request()[0], 200)


if __name__ == "__main__":
    unittest.main()
