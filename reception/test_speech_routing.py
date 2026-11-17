from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from speech_controller.lifecycle import State
from speech_controller.readiness import ProbeTarget
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from .speech import SpeechBusy, SpeechUnavailable, synthesize, synthesize_welcome, transcribe
from .test_speech import wav_fixture


TARGETS = (ProbeTarget("tts-old", "tts", "http://old-tts:5000", ("en", "pl")),
           ProbeTarget("tts-new", "tts", "http://new-tts:5000", ("en", "pl")),
           ProbeTarget("stt", "stt", "http://recognizer:5001", ("en", "pl")))
ROUTES = {(language, task): "stt" if task == "stt" else "tts-old"
          for language in ("en", "pl") for task in ("stt", "tts")}


@override_settings(HOTEL_PIPER_URL="", HOTEL_ASR_URL="")
class SpeechRoutingTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "routing.sqlite3"
        initialize_routing(self.path, TARGETS, ROUTES)
        self.gate = RoutedTrafficGate(self.path, TARGETS)
        self.raw = json.dumps([asdict(target) for target in TARGETS])
        override = override_settings(HOTEL_SPEECH_ROUTE_TARGETS=self.raw,
                                     HOTEL_SPEECH_TRAFFIC_DB=str(self.path))
        override.enable()
        self.addCleanup(override.disable)

    def switch(self, language="en"):
        current = self.gate.state
        routes = dict(current.routes) | {(language, "tts"): "tts-new"}
        destination = State(current.revision + 1, tuple(sorted(routes.items())))
        self.assertTrue(self.gate.drain((language, "tts")))
        self.assertTrue(self.gate.publish(current, destination))
        self.assertTrue(self.gate.resume((language, "tts")))

    def test_routed_requests_use_selected_endpoint_and_preserve_payload(self):
        with patch("reception.speech.build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = wav_fixture()
            synthesize("Welcome", "en")
            first = build.return_value.open.call_args.args[0]
            self.assertEqual(first.full_url, "http://old-tts:5000/synthesize")
            self.assertEqual(json.loads(first.data), {"text": "Welcome", "language": "en"})
            self.switch()
            synthesize("Welcome", "en")
            self.assertEqual(build.return_value.open.call_args.args[0].full_url, "http://new-tts:5000/synthesize")
            synthesize("Witamy", "pl")
            self.assertEqual(build.return_value.open.call_args.args[0].full_url, "http://old-tts:5000/synthesize")
            response.read.return_value = b'{"text":"Tomorrow"}'
            self.assertEqual(transcribe(b"\0\0", "en"), "Tomorrow")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://recognizer:5001/transcribe?language=en")
            self.assertEqual(request.data, b"\0\0")

    def test_route_publication_leaves_speech_paused_until_resume(self):
        current = self.gate.state
        destination = State(1, tuple(sorted((ROUTES | {("en", "tts"): "tts-new"}).items())))
        self.assertTrue(self.gate.drain(("en", "tts")))
        self.assertTrue(self.gate.publish(current, destination))
        with patch("reception.speech.build_opener") as build, self.assertRaises(SpeechBusy):
            synthesize("Welcome", "en")
        build.return_value.open.assert_not_called()

    def test_invalid_or_drifted_configuration_never_uses_fixed_fallback(self):
        for raw, path in (("not json", str(self.path)), (self.raw, ""),
                          (self.raw.replace("old-tts:5000", "different:5000"), str(self.path)),
                          ("", str(self.path))):
            with self.subTest(raw=raw), override_settings(HOTEL_SPEECH_ROUTE_TARGETS=raw,
                    HOTEL_SPEECH_TRAFFIC_DB=path, HOTEL_PIPER_URL="http://fallback:5000"):
                with patch("reception.speech.build_opener") as build, self.assertRaises(SpeechUnavailable):
                    synthesize("Welcome", "en")
                build.return_value.open.assert_not_called()

    def test_welcome_audio_is_regenerated_after_provider_change(self):
        with patch("reception.speech.build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = wav_fixture()
            parts = (("en", "Welcome"),)
            synthesize_welcome(parts)
            self.switch()
            synthesize_welcome(parts)
            self.assertEqual([call.args[0].full_url for call in build.return_value.open.call_args_list],
                             ["http://old-tts:5000/synthesize", "http://new-tts:5000/synthesize"])

    def test_public_speech_works_with_routing_and_no_fixed_endpoints(self):
        page = self.client.get("/?lang=en")
        self.assertTrue(page.context["piper_enabled"])
        self.assertTrue(page.context["asr_enabled"])
        started = self.client.post(reverse("reception-message"), {"language": "en", "start": True},
                                   content_type="application/json").json()
        with patch("reception.speech.build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = wav_fixture()
            audio = self.client.post(reverse("reception-audio"), {"revision": started["revision"], "part": 0},
                                     content_type="application/json")
            self.assertEqual(audio.status_code, 200)
            response.read.return_value = b'{"text":"Tomorrow"}'
            transcript = self.client.post(reverse("reception-transcript"), b"\0\0",
                content_type="application/octet-stream", headers={"X-Reception-Revision": started["revision"]})
            self.assertEqual(transcript.json(), {"text": "Tomorrow"})
