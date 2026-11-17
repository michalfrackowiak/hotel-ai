"""Real child-process cancellation for peers that never become socket-idle."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.readiness import ProbeTarget, ReadinessProbes, _probe_once


class TrickleHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        mode = self.server.mode
        self.server.requests.append(mode)
        try:
            if mode == "healthy":
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{"text":""}')
                return
            if mode == "body":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n")
                self.wfile.flush()
            else:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Trickling: ")
            for _ in range(500):
                self.wfile.write(b" ")
                self.wfile.flush()
                if self.server.stop.wait(0.03):
                    break
        except (ConnectionError, OSError):
            pass

    def log_message(self, *_args):
        pass


class DeadlineTests(unittest.TestCase):
    def test_cold_listener_can_start_within_the_budget_and_receives_one_post(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), TrickleHandler)
        server.mode = "healthy"
        server.requests = []
        target = ProbeTarget("asr", "stt", f"http://127.0.0.1:{server.server_port}", ("en",))
        original_connect = HTTPConnection.connect
        attempts = []

        def connect(connection):
            attempts.append(connection.host)
            if len(attempts) == 1:
                raise ConnectionRefusedError
            original_connect(connection)

        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            with patch.object(HTTPConnection, "connect", connect):
                self.assertTrue(_probe_once(target, "en"))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(server.requests, ["healthy"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_trickling_headers_and_body_are_killed_reaped_and_can_recover(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), TrickleHandler)
        server.stop = Event()
        server.requests = []
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        target = ProbeTarget("asr", "stt", f"http://127.0.0.1:{server.server_port}", ("en", "pl"))
        children = []
        original_popen = subprocess.Popen

        def record_child(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        try:
            with patch("speech_controller.readiness.subprocess.Popen", side_effect=record_child):
                for mode in ("header", "body"):
                    with self.subTest(mode=mode):
                        server.mode = mode
                        started = monotonic()
                        self.assertFalse(ReadinessProbes([target], timeout=1)("asr", ("en", "stt")))
                        self.assertLess(monotonic() - started, 2.5)
                        self.assertEqual(server.requests[-1], mode)
                        child = children[-1]
                        self.assertIsNotNone(child.poll())
                        self.assertNotEqual(child.returncode, 0)
                        if os.name == "posix":
                            with self.assertRaises(ChildProcessError):
                                os.waitpid(child.pid, os.WNOHANG)
                        server.mode = "healthy"
                        self.assertTrue(ReadinessProbes([target])("asr", ("pl", "stt")))
                        self.assertEqual(children[-1].returncode, 0)
            self.assertEqual(server.requests, ["header", "healthy", "body", "healthy"])
            self.assertEqual(len(children), 4)
        finally:
            server.stop.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_worker_spawn_failure_and_nonzero_exit_fail_without_retry(self):
        probes = ReadinessProbes([ProbeTarget("asr", "stt", "http://asr:5001", ("en",))])
        with patch("speech_controller.readiness.subprocess.Popen", side_effect=OSError) as start:
            self.assertFalse(probes("asr", ("en", "stt")))
            start.assert_called_once()
        with patch("speech_controller.readiness.subprocess.Popen") as start:
            child = start.return_value.__enter__.return_value
            child.returncode = 23
            child.poll.return_value = 23
            self.assertFalse(probes("asr", ("en", "stt")))
            start.assert_called_once()
            child.wait.assert_called_once()
            child.kill.assert_not_called()
            self.assertEqual(start.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(start.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_startup_consumes_budget_and_late_success_is_rejected(self):
        probes = ReadinessProbes([ProbeTarget("asr", "stt", "http://asr:5001", ("en",))])
        with patch("speech_controller.readiness.subprocess.Popen") as start:
            child = start.return_value.__enter__.return_value
            child.returncode = 0
            child.poll.return_value = 0
            with patch("speech_controller.readiness.monotonic", side_effect=[0, 9, 9]):
                self.assertFalse(probes("asr", ("en", "stt")))
            self.assertEqual(child.communicate.call_args.kwargs["timeout"], 0)
            child.wait.assert_called_once()

    def test_timeout_configuration_rejects_unbounded_and_invalid_values(self):
        target = ProbeTarget("asr", "stt", "http://asr:5001", ("en",))
        for value in (0, -1, 9, 10**400, True, None, "1", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ReadinessProbes([target], timeout=value)


if __name__ == "__main__":
    unittest.main()
