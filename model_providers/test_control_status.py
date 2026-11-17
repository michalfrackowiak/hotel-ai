from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .control_status import ControlStatusError, control_status_report, fetch_control_status, validate_status
from .models import SpeechDeployment, SpeechSelection


TOKEN = "fictional-status-token-" + "x" * 32


def snapshot():
    routes = [[[language, task], task + "-worker"]
              for language in ("en", "pl") for task in ("stt", "tts")]
    return dict(state=dict(revision=2, routes=routes), observed=dict(revision=2, routes=deepcopy(routes)),
                blocked=False, pending_requests=[], pending_recoveries=[])


@contextmanager
def controller(body=None, status=200, trickle=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            raw = json.dumps(snapshot()).encode() if body is None else body
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                if trickle:
                    for character in raw:
                        self.wfile.write(bytes([character]))
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


class ControlStatusTests(SimpleTestCase):
    def test_real_status_transport_uses_only_authenticated_get(self):
        with controller() as calls:
            self.assertEqual(fetch_control_status(), snapshot())
        self.assertEqual(calls, [("/v1/status", "Bearer " + TOKEN)])

    def test_status_report_distinguishes_route_disagreement_and_pending_work(self):
        data = snapshot()
        data["observed"]["revision"] = 3
        data["observed"]["routes"][0][1] = "new-worker"
        with patch("model_providers.control_status.fetch_control_status", return_value=data):
            report = control_status_report()
        self.assertTrue(report["needs_attention"])
        self.assertTrue(report["route_mismatch"])
        self.assertFalse(report["controller_blocked"])
        self.assertEqual(len(report["status_rows"]), 4)
        data.update(blocked=True, pending_requests=["switch-1"], pending_recoveries=["recover-1"])
        with patch("model_providers.control_status.fetch_control_status", return_value=data):
            self.assertEqual(control_status_report()["pending_recoveries"], ["recover-1"])

    @override_settings(HOTEL_SPEECH_CONTROL_URL="")
    def test_disabled_configuration_makes_no_request(self):
        with patch("model_providers.control_status.subprocess.run") as run:
            self.assertEqual(control_status_report()["status_error_code"], "disabled")
            run.assert_not_called()

    def test_invalid_configuration_never_starts_transport(self):
        for url in ("http://localhost:8093", "https://127.0.0.1:8093", "http://127.0.0.1:8093/",
                    "http://127.0.0.1", "http://127.0.0.1:8093?token=x", "http://example.test:8093"):
            with self.subTest(url=url), override_settings(HOTEL_SPEECH_CONTROL_URL=url,
                    HOTEL_SPEECH_CONTROL_TOKEN=TOKEN), patch("model_providers.control_status.subprocess.run") as run:
                self.assertEqual(control_status_report()["status_error_code"], "configuration")
                run.assert_not_called()

    def test_strict_snapshot_rejects_ambiguous_routes_and_pending_state(self):
        invalid = [snapshot() | {"extra": 1}, snapshot() | {"blocked": 1},
                   snapshot() | {"pending_requests": ["pending"]},
                   snapshot() | {"blocked": True, "pending_recoveries": ["same", "same"]},
                   snapshot() | {"blocked": True, "pending_requests": [str(i) for i in range(129)]}]
        for change in ({"revision": True}, {"revision": -1}, {"revision": 2**63}, {"routes": []}):
            invalid.append(snapshot() | {"state": snapshot()["state"] | change})
        duplicate = snapshot()
        duplicate["observed"]["routes"][1] = duplicate["observed"]["routes"][0]
        invalid.append(duplicate)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_status(data)

    def test_http_errors_redirects_and_malformed_bodies_are_sanitized(self):
        for status, code in ((429, "busy"), (401, "unauthorized"), (503, "unavailable"), (302, "unavailable")):
            with self.subTest(status=status), controller(body=TOKEN.encode(), status=status) as calls:
                report = control_status_report()
                self.assertEqual(report["status_error_code"], code)
                self.assertNotIn(TOKEN, report["status_error"])
                self.assertEqual(len(calls), 1)
        for body in (b"x" * 65537, b'{"blocked":false,"blocked":true}', b'{"blocked":NaN}', b"\xff", b"[]"):
            with self.subTest(body=body[:30]), controller(body=body):
                self.assertEqual(control_status_report()["status_error_code"], "invalid")

    def test_total_deadline_stops_trickling_response_and_next_request_recovers(self):
        with controller(trickle=True), patch("model_providers.control_status.FETCH_TIMEOUT", 0.7):
            started = monotonic()
            self.assertEqual(control_status_report()["status_error_code"], "unavailable")
            self.assertLess(monotonic() - started, 3)
        with controller():
            self.assertEqual(fetch_control_status(), snapshot())

    def test_worker_receives_credentials_only_on_stdin_and_ignores_proxies(self):
        import os
        import subprocess
        run = subprocess.run
        with controller(), patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}), \
                patch("model_providers.control_status.subprocess.run", wraps=run) as invoked:
            self.assertEqual(fetch_control_status(), snapshot())
            args, kwargs = invoked.call_args
            self.assertNotIn(TOKEN, " ".join(args[0]))
            self.assertIn(TOKEN.encode(), kwargs["input"])
            self.assertEqual(kwargs["timeout"], 6)


class ControlStatusAdminTests(TestCase):
    def test_staff_permissions_get_only_and_no_plan_writes(self):
        user = get_user_model().objects.create_user("status-viewer", is_staff=True)
        url = reverse("admin:model_providers_speechselection_controller_status")
        with patch("model_providers.control_status.fetch_control_status", return_value=snapshot()) as fetch:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            fetch.assert_not_called()
            for model in ("speechselection", "speechdeployment", "speechprovider", "speechmodel"):
                user.user_permissions.add(Permission.objects.get(codename="view_" + model))
            before = list(SpeechDeployment.objects.values()), list(SpeechSelection.objects.values())
            response = self.client.get(url)
            self.assertContains(response, "Journal revision: 2")
            self.assertContains(response, "English", count=2)
            self.assertContains(response, "Polish", count=2)
            self.assertNotContains(response, TOKEN)
            self.assertIn("no-store", response.headers["Cache-Control"])
            fetch.reset_mock()
            self.assertEqual(self.client.post(url).status_code, 405)
            fetch.assert_not_called()
            self.assertEqual(before, (list(SpeechDeployment.objects.values()), list(SpeechSelection.objects.values())))
            user.user_permissions.remove(Permission.objects.get(codename="view_speechmodel"))
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_attention_pending_and_disabled_states_render(self):
        user = get_user_model().objects.create_superuser("status-admin", password="fictional")
        self.client.force_login(user)
        url = reverse("admin:model_providers_speechselection_controller_status")
        data = snapshot()
        data["observed"]["revision"] = 3
        data.update(blocked=True, pending_requests=["switch-1"], pending_recoveries=["recover-1"])
        with patch("model_providers.control_status.fetch_control_status", return_value=data):
            response = self.client.get(url)
            for text in ("Attention required", "routes disagree", "switch-1", "recover-1", "Observed route revision: 3"):
                self.assertContains(response, text)
        with override_settings(HOTEL_SPEECH_CONTROL_URL=""):
            self.assertContains(self.client.get(url), "not configured")
        response = self.client.get(reverse("admin:model_providers_speechselection_changelist"))
        self.assertContains(response, url)
