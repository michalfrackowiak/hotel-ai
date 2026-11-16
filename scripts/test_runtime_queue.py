"""Exercise both runtime admission queues over real HTTP without model weights."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import sys
from threading import Event, Lock, Thread
from time import monotonic
from types import ModuleType
import unittest
from unittest.mock import patch


def load_runtime(directory):
    dependencies = {}
    for name, attributes in {
        "onnxruntime": (),
        "piper": ("PiperConfig", "PiperVoice"),
        "vosk": ("KaldiRecognizer", "Model", "SetLogLevel"),
    }.items():
        module = ModuleType(name)
        for attribute in attributes:
            setattr(module, attribute, object())
        dependencies[name] = module
    spec = importlib.util.spec_from_file_location(directory, Path(__file__).resolve().parents[1] / directory / "service.py")
    runtime = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(runtime)
    return runtime


RUNTIMES = [load_runtime(name) for name in ("piper_runtime", "asr_runtime")]


def wait_for(predicate, timeout=2):
    until = monotonic() + timeout
    delay = Event()
    while not predicate():
        if monotonic() >= until:
            raise AssertionError("Timed out waiting for queue state.")
        delay.wait(0.005)


class InferenceProbe:
    def __init__(self, fail_first=False):
        self.entered = Event()
        self.release = Event()
        self.lock = Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.fail_first = fail_first

    def infer(self, *arguments):
        with self.lock:
            self.calls += 1
            number = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if number == 1:
                self.entered.set()
                if not self.release.wait(4):
                    raise RuntimeError("Test inference was never released.")
                if self.fail_first:
                    raise RuntimeError("Fictional inference failure")
            return b"fixture-wav" if isinstance(arguments[0], str) else "fixture transcript"
        finally:
            with self.lock:
                self.active -= 1


@contextmanager
def running(runtime, *, timeout=3, fail_first=False):
    piper = runtime.__name__ == "piper_runtime"
    server_type = runtime.PiperServer if piper else runtime.RecognitionServer
    handler = runtime.PiperHandler if piper else runtime.RecognitionHandler
    server = server_type.__new__(server_type)
    server.inference = runtime.InferenceQueue()
    server.connections = runtime.threading.BoundedSemaphore(runtime.MAX_CONNECTIONS)
    if piper:
        server.voices = {"en": object(), "pl": object()}
    else:
        server.models = {"en": object(), "pl": object()}
    ThreadingHTTPServer.__init__(server, ("127.0.0.1", 0), handler)
    probe = InferenceProbe(fail_first)
    target = patch.object(handler, "synthesize", staticmethod(probe.infer)) if piper else patch.object(runtime, "transcribe", probe.infer)
    with target, patch.object(runtime, "QUEUE_WAIT_SECONDS", timeout):
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, probe
        finally:
            probe.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def request_data(runtime, language):
    if runtime.__name__ == "piper_runtime":
        return "/synthesize", json.dumps({"language": language, "text": "fictional phrase"}).encode(), "application/json"
    return "/transcribe?language=" + language, b"\x01\x00" * 100, "application/octet-stream"


def post(runtime, server, language="en"):
    route, body, content_type = request_data(runtime, language)
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request("POST", route, body, {"Content-Type": content_type})
        response = client.getresponse()
        data = response.read()
        return response.status, response.getheader("Retry-After"), data
    finally:
        client.close()


class RuntimeQueueTests(unittest.TestCase):
    def test_cross_language_waiter_runs_after_active_and_third_request_is_rejected(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__name__), running(runtime) as (server, probe), ThreadPoolExecutor(2) as pool:
                first = pool.submit(post, runtime, server)
                self.assertTrue(probe.entered.wait(2))
                second = pool.submit(post, runtime, server, "pl")
                wait_for(lambda: server.inference.waiting)
                started = monotonic()
                self.assertEqual(post(runtime, server)[:2], (429, "1"))
                self.assertLess(monotonic() - started, 1)
                self.assertFalse(second.done())
                self.assertEqual(probe.calls, 1)
                probe.release.set()
                self.assertEqual(first.result()[0], 200)
                self.assertEqual(second.result()[0], 200)
                self.assertEqual(probe.max_active, 1)
                self.assertEqual(post(runtime, server)[0], 200)

    def test_waiter_timeout_releases_capacity_and_never_runs(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__name__), running(runtime, timeout=0.15) as (server, probe), ThreadPoolExecutor(1) as pool:
                first = pool.submit(post, runtime, server)
                self.assertTrue(probe.entered.wait(2))
                self.assertEqual(post(runtime, server, "pl")[:2], (429, "1"))
                self.assertFalse(server.inference.waiting)
                self.assertEqual(probe.calls, 1)
                probe.release.set()
                self.assertEqual(first.result()[0], 200)
                self.assertEqual(post(runtime, server)[0], 200)

    def test_disconnected_waiter_is_removed_without_inference_and_next_waiter_can_run(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__name__), running(runtime) as (server, probe), ThreadPoolExecutor(2) as pool:
                first = pool.submit(post, runtime, server)
                self.assertTrue(probe.entered.wait(2))
                route, body, content_type = request_data(runtime, "pl")
                client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
                headers = f"POST {route} HTTP/1.0\r\nHost: localhost\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
                client.sendall(headers.encode() + body)
                wait_for(lambda: server.inference.waiting)
                client.close()
                wait_for(lambda: not server.inference.waiting)
                self.assertEqual(probe.calls, 1)
                next_waiter = pool.submit(post, runtime, server, "pl")
                wait_for(lambda: server.inference.waiting)
                probe.release.set()
                self.assertEqual(first.result()[0], 200)
                self.assertEqual(next_waiter.result()[0], 200)
                self.assertEqual(probe.calls, 2)

    def test_inference_exception_releases_active_slot_for_waiting_request(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime.__name__), running(runtime, fail_first=True) as (server, probe), ThreadPoolExecutor(2) as pool:
                first = pool.submit(post, runtime, server)
                self.assertTrue(probe.entered.wait(2))
                second = pool.submit(post, runtime, server, "pl")
                wait_for(lambda: server.inference.waiting)
                probe.release.set()
                self.assertEqual(first.result()[0], 503)
                self.assertEqual(second.result()[0], 200)
                self.assertEqual(probe.max_active, 1)


if __name__ == "__main__":
    unittest.main()
