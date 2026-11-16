from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .controller_client import InventoryError, fetch_inventory, inventory_report
from .models import SpeechDeployment, SpeechProvider, SpeechSelection


TOKEN = "fictional-controller-token-1234567890"
IMAGE = "example.test/speech@sha256:" + "a" * 64


def snapshot():
    return {"host": "fixture-host", "observed_at": datetime.now(timezone.utc).isoformat(),
            "deployments": [{"container_name": "fixture-asr", "id": "b" * 64,
                "state": "running", "health": "healthy", "image_id": "sha256:" + "c" * 64,
                "configured_image": IMAGE, "cpu_limit": 1, "memory_mib": 1024}]}


@contextmanager
def controller_response(body=None, redirect=False, malformed_status=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            if malformed_status:
                self.wfile.write(b"secret malformed status\r\n\r\n")
                return
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header("Location", "/must-not-follow")
            raw = body if body is not None else json.dumps(snapshot()).encode()
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with override_settings(HOTEL_SPEECH_CONTROLLER_URL=f"http://127.0.0.1:{server.server_port}",
                                   HOTEL_SPEECH_CONTROLLER_TOKEN=TOKEN,
                                   HOTEL_SPEECH_CONTROLLER_HOST="fixture-host"):
                yield calls
        finally:
            server.shutdown()
            thread.join(timeout=2)


class ControllerTransportTests(SimpleTestCase):
    def test_real_http_auth_and_redirect_never_forwarded(self):
        with controller_response() as calls, patch.dict("os.environ", {"http_proxy": "http://invalid.test:1"}):
            self.assertEqual(fetch_inventory()["host"], "fixture-host")
            self.assertEqual(calls, [("/v1/inventory", "Bearer " + TOKEN)])
        with controller_response(redirect=True) as calls:
            with self.assertRaises(InventoryError) as caught:
                fetch_inventory()
            self.assertEqual(len(calls), 1)
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIn("must-not-follow", str(caught.exception))

    def test_oversized_malformed_and_stale_responses_rejected(self):
        invalid = [b"x" * 65537, b"secret-invalid-json", b"[]", b"[" * 2000 + b"]" * 2000,
                   json.dumps(snapshot()).replace('"host": "fixture-host"',
                       '"host": "wrong", "host": "fixture-host"').encode()]
        for change in ({"host": "wrong"}, {"observed_at": "bad"}, {"observed_at": "2026-01-01T00:00:00"},
                       {"observed_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()},
                       {"observed_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}):
            invalid.append(json.dumps(snapshot() | change).encode())
        for change in ({"id": "bad"}, {"state": "unknown"}, {"health": "bad"}, {"cpu_limit": True},
                       {"memory_mib": float("nan")}, {"cpu_limit": -1}):
            data = snapshot()
            data["deployments"][0].update(change)
            invalid.append(json.dumps(data).encode())
        for raw in invalid:
            with self.subTest(raw=raw[:40]), controller_response(raw):
                with self.assertRaises(InventoryError) as caught:
                    fetch_inventory()
                self.assertNotIn("secret", str(caught.exception))
        with controller_response(malformed_status=True):
            with self.assertRaises(InventoryError) as caught:
                fetch_inventory()
            self.assertNotIn("secret", str(caught.exception))

    def test_disabled_and_invalid_configuration_never_calls_network(self):
        for url in ("", "http://example.test", "http://localhost:8092", "https://user:pass@example.test",
                    "https://example.test/path", "https://example.test?x=y", "https://example.test#fragment",
                    "https://exam\nple.test", "http://127.0.0.1:70000"):
            with self.subTest(url=url), override_settings(HOTEL_SPEECH_CONTROLLER_URL=url,
                    HOTEL_SPEECH_CONTROLLER_TOKEN=TOKEN, HOTEL_SPEECH_CONTROLLER_HOST="fixture-host"), \
                    patch("model_providers.controller_client.build_opener") as opener:
                with self.assertRaises(InventoryError):
                    fetch_inventory()
                opener.assert_not_called()


class ControllerAdminTests(TestCase):
    def test_permissions_snapshot_comparison_and_no_writes(self):
        provider = SpeechProvider.objects.create(name="fixture", slug="fixture", task="stt", engine="fixture",
            languages=["en", "pl"], architecture="arm64", image=IMAGE)
        deployment = SpeechDeployment.objects.create(name="<script>fixture</script>", slug="fixture",
            provider=provider, host="fixture-host", container_name="fixture-asr")
        SpeechDeployment.objects.create(name="Missing", slug="missing", provider=provider,
            host="fixture-host", container_name="not-observed")
        user = get_user_model().objects.create_user("viewer", is_staff=True)
        url = reverse("admin:model_providers_speechselection_inventory")
        with patch("model_providers.controller_client.fetch_inventory", return_value=snapshot()) as read:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            read.assert_not_called()
            for model in ("speechselection", "speechdeployment", "speechprovider", "speechmodel"):
                user.user_permissions.add(Permission.objects.get(codename="view_" + model))
            before = list(SpeechDeployment.objects.values()), list(SpeechSelection.objects.values())
            response = self.client.get(url)
            self.assertContains(response, "&lt;script&gt;fixture&lt;/script&gt;")
            self.assertContains(response, "Configured image matches: Yes")
            self.assertContains(response, "Configured limits match: Yes")
            self.assertContains(response, "Unknown: not present")
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(self.client.post(url).status_code, 405)
            self.assertEqual(before, (list(SpeechDeployment.objects.values()), list(SpeechSelection.objects.values())))
            deployment.refresh_from_db()
            self.assertEqual(deployment.observed_state, "unknown")
            user.user_permissions.remove(Permission.objects.get(codename="view_speechmodel"))
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_unmanaged_observation_and_disabled_message(self):
        with patch("model_providers.controller_client.fetch_inventory", return_value=snapshot()):
            self.assertIsNone(inventory_report()["rows"][0]["planned"])
        with override_settings(HOTEL_SPEECH_CONTROLLER_URL=""):
            report = inventory_report()
            self.assertEqual(report["rows"], [])
            self.assertIn("not configured", report["inventory_error"])
