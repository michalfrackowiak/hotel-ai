from http.client import IncompleteRead
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import TestCase, override_settings
from django.urls import reverse

from speech_controller.traffic import TrafficGate, initialize
from .models import Conversation
from .speech import SpeechBusy, SpeechUnavailable, synthesize, transcribe
from .test_speech import wav_fixture


@override_settings(HOTEL_PIPER_URL="http://piper:5000", HOTEL_ASR_URL="http://asr:5001")
class SpeechTrafficTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "traffic.sqlite3"
        initialize(self.path)
        self.gate = TrafficGate(self.path, timeout=0.5)
        override = override_settings(HOTEL_SPEECH_TRAFFIC_DB=str(self.path))
        override.enable()
        self.addCleanup(override.disable)

    def test_complete_responses_release_all_four_slots(self):
        with patch("reception.speech.build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            for language in ("en", "pl"):
                response.read.return_value = wav_fixture()
                self.assertEqual(synthesize("Welcome", language), wav_fixture())
                response.read.return_value = b'{"text":"Tomorrow"}'
                self.assertEqual(transcribe(b"\0\0", language), "Tomorrow")
                for task in ("tts", "stt"):
                    self.assertTrue(self.gate.drain((language, task)))
                    self.assertTrue(self.gate.resume((language, task)))

    def test_closed_slot_blocks_before_http_and_other_language_still_works(self):
        self.assertTrue(self.gate.drain(("en", "tts")))
        self.assertTrue(self.gate.drain(("en", "stt")))
        with patch("reception.speech.build_opener") as build:
            with self.assertRaises(SpeechBusy):
                synthesize("Welcome", "en")
            with self.assertRaises(SpeechBusy):
                transcribe(b"\0\0", "en")
            build.return_value.open.assert_not_called()
            build.return_value.open.return_value.__enter__.return_value.read.return_value = wav_fixture()
            self.assertEqual(synthesize("Witamy", "pl"), wav_fixture())

    def test_ambiguous_open_and_read_failures_retain_claims(self):
        for language, reading in (("en", False), ("pl", True)):
            with self.subTest(language=language), patch("reception.speech.build_opener") as build:
                if reading:
                    build.return_value.open.return_value.__enter__.return_value.read.side_effect = IncompleteRead(b"partial")
                else:
                    build.return_value.open.side_effect = TimeoutError
                with self.assertRaises(SpeechUnavailable):
                    transcribe(b"\0\0", language)
                self.assertFalse(self.gate.drain((language, "stt")))
                self.assertFalse(self.gate.resume((language, "stt")))
                with self.assertRaises(SpeechBusy):
                    transcribe(b"\0\0", language)
                self.assertEqual(build.return_value.open.call_count, 1)

    def test_busy_response_releases_but_other_http_error_stays_uncertain(self):
        with patch("reception.speech.build_opener") as build:
            build.return_value.open.side_effect = HTTPError("http://asr", 429, "busy", {}, None)
            with self.assertRaises(SpeechBusy):
                transcribe(b"\0\0", "en")
            self.assertTrue(self.gate.drain(("en", "stt")))
            self.assertTrue(self.gate.resume(("en", "stt")))
            build.return_value.open.side_effect = HTTPError("http://asr", 503, "unavailable", {}, None)
            with self.assertRaises(SpeechUnavailable):
                transcribe(b"\0\0", "en")
            self.assertFalse(self.gate.drain(("en", "stt")))

    def test_incomplete_or_oversized_response_stays_uncertain(self):
        for language, body, remaining in (("en", b'{"text":""}', 5),
                                           ("pl", b"x" * 8193, 0)):
            with self.subTest(language=language), patch("reception.speech.build_opener") as build:
                response = build.return_value.open.return_value.__enter__.return_value
                response.read.return_value, response.length = body, remaining
                with self.assertRaises(SpeechUnavailable):
                    transcribe(b"\0\0", language)
                self.assertFalse(self.gate.drain((language, "stt")))
                self.assertFalse(self.gate.resume((language, "stt")))

    def test_missing_or_corrupt_configured_store_does_not_send_or_recreate(self):
        missing = self.path.with_name("missing.sqlite3")
        corrupt = self.path.with_name("corrupt.sqlite3")
        corrupt.write_bytes(b"invalid database")
        for path in (missing, corrupt):
            with self.subTest(path=path), override_settings(HOTEL_SPEECH_TRAFFIC_DB=str(path)):
                with patch("reception.speech.build_opener") as build, self.assertRaises(SpeechUnavailable):
                    transcribe(b"\0\0", "en")
                build.return_value.open.assert_not_called()
        self.assertFalse(missing.exists())

    def test_public_busy_responses_preserve_booking_and_chat_access(self):
        self.client.get("/?lang=en")
        started = self.client.post(reverse("reception-message"), {"language": "en", "start": True},
                                   content_type="application/json").json()
        before = Conversation.objects.get().state
        self.gate.drain(("en", "tts"))
        self.gate.drain(("en", "stt"))
        with patch("reception.speech.build_opener") as build:
            audio = self.client.post(reverse("reception-audio"),
                                     {"revision": started["revision"], "part": 0},
                                     content_type="application/json")
            transcript = self.client.post(reverse("reception-transcript"), b"\0\0",
                                          content_type="application/octet-stream",
                                          headers={"X-Reception-Revision": started["revision"]})
            build.return_value.open.assert_not_called()
        for response in (audio, transcript):
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json(), {"error": "speech_busy"})
        self.assertEqual(Conversation.objects.get().state, before)
        reply = self.client.post(reverse("reception-message"),
                                 {"language": "en", "revision": started["revision"], "message": "from tomorrow for three days"},
                                 content_type="application/json")
        self.assertEqual(reply.status_code, 200)
