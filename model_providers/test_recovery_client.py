from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .recovery_client import RecoveryTransportError, submit_recovery
from .test_control_status import TOKEN, snapshot


PAYLOAD = dict(recovery_id="admin-fixture", expected_revision=2,
               observed=snapshot()["observed"], reason="Verified fictional workers")
RESULT = dict(status="reconciled", reason="operator_reconciled", state=PAYLOAD["observed"])


@contextmanager
def recovery_server(body=None, status=200, trickle=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization"),
                          json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            raw = json.dumps(RESULT).encode() if body is None else body
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                if trickle:
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        sleep(0.05)
                else:
                    self.wfile.write(raw)
            except OSError:
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            with override_settings(HOTEL_SPEECH_CONTROL_URL=f"http://127.0.0.1:{server.server_port}",
                                   HOTEL_SPEECH_CONTROL_TOKEN=TOKEN):
                yield calls
        finally:
            server.shutdown()
            thread.join(2)


class RecoveryClientTests(SimpleTestCase):
    def test_single_authenticated_post_preserves_payload_and_hides_token_from_argv(self):
        run = subprocess.run
        with recovery_server() as calls, patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}), \
                patch("model_providers.control_operation.subprocess.run", wraps=run) as process:
            self.assertEqual(submit_recovery(deepcopy(PAYLOAD)), RESULT)
        self.assertEqual(calls, [("/v1/recover", "Bearer " + TOKEN, PAYLOAD)])
        args, kwargs = process.call_args
        self.assertNotIn(TOKEN, " ".join(args[0]))
        self.assertIn(TOKEN.encode(), kwargs["input"])
        self.assertEqual(kwargs["timeout"], 20)

    def test_failed_http_or_redirect_never_retries_and_keeps_uncertainty(self):
        for status in (302, 400, 401, 429, 503):
            with self.subTest(status=status), recovery_server(TOKEN.encode(), status) as calls:
                with self.assertRaises(RecoveryTransportError) as error:
                    submit_recovery(PAYLOAD)
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertIn("unconfirmed", str(error.exception))
                self.assertEqual(len(calls), 1)

    def test_invalid_or_unrelated_success_is_never_trusted(self):
        wrong_state = deepcopy(RESULT)
        wrong_state["state"]["revision"] += 1
        bodies = [b"x" * 65537, b'{}', b'{"status":"reconciled","status":"rejected"}',
                  json.dumps(wrong_state).encode(), json.dumps(RESULT | {"reason": "other"}).encode(),
                  json.dumps(RESULT | {"status": "succeeded"}).encode()]
        for body in bodies:
            with self.subTest(body=body[:30]), recovery_server(body) as calls:
                with self.assertRaises(RecoveryTransportError):
                    submit_recovery(PAYLOAD)
                self.assertEqual(len(calls), 1)

    def test_domain_rejection_is_returned_without_claiming_success(self):
        result = RESULT | {"status": "rejected", "reason": "reconciliation_unverified"}
        with recovery_server(json.dumps(result).encode()):
            self.assertEqual(submit_recovery(PAYLOAD), result)

    def test_maximum_length_unicode_reason_fits_controller_body_limit(self):
        payload = PAYLOAD | {"reason": "🛠" * 500}
        with recovery_server() as calls:
            self.assertEqual(submit_recovery(payload), RESULT)
        self.assertEqual(calls[0][2], payload)

    def test_deadline_leaves_outcome_uncertain_and_next_attempt_reuses_payload(self):
        with recovery_server(trickle=True) as calls, patch("model_providers.recovery_client.RECOVERY_TIMEOUT", 0.7):
            started = monotonic()
            with self.assertRaises(RecoveryTransportError):
                submit_recovery(PAYLOAD)
            self.assertLess(monotonic() - started, 3)
            self.assertEqual(calls[0][2], PAYLOAD)
        with recovery_server() as calls:
            self.assertEqual(submit_recovery(PAYLOAD), RESULT)
            self.assertEqual(calls[0][2], PAYLOAD)

    @override_settings(HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:8093", HOTEL_SPEECH_CONTROL_TOKEN=TOKEN)
    def test_invalid_payload_makes_no_request(self):
        for payload in (PAYLOAD | {"actor": "spoof"}, PAYLOAD | {"expected_revision": True},
                        PAYLOAD | {"reason": ""}, PAYLOAD | {"observed": {}}):
            with self.subTest(payload=payload), patch("model_providers.control_operation.subprocess.run") as run:
                with self.assertRaises(RecoveryTransportError):
                    submit_recovery(payload)
                run.assert_not_called()
