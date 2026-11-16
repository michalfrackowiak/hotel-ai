import io
import json
import wave
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .conversation import ARRIVAL_PARTS, WELCOME_PARTS, new_conversation
from .models import Conversation, ScenarioPrompt
from .speech import MAX_AUDIO_BYTES, SpeechBusy, SpeechUnavailable, generation_slot, speech_slots, synthesize, transcribe


def wav_fixture(*, channels=1, sample_width=2, rate=22050):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((channels, sample_width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00" * channels * sample_width * 16)
    return output.getvalue()


@override_settings(HOTEL_PIPER_URL="http://piper:5000")
class ReceptionAudioTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get("/?lang=pl").cookies["csrftoken"].value
        self.started = self.start_conversation()
        self.payload = {"revision": self.started["revision"]}

    def start_conversation(self, action="start", *, at_welcome=False):
        started = self.client.post(
            reverse("reception-message"),
            {"language": "pl", action: True},
            content_type="application/json",
            headers={"X-CSRFToken": self.token},
        ).json()
        if at_welcome:
            return started
        return self.client.post(
            reverse("reception-message"),
            {"language": "pl", "revision": started["revision"], "timeout": True},
            content_type="application/json",
            headers={"X-CSRFToken": self.token},
        ).json()

    def request_audio(self, payload, *, token=None):
        headers = {} if token is None else {"X-CSRFToken": token}
        return self.client.post(reverse("reception-audio"), payload, content_type="application/json", headers=headers)

    @patch("reception.views.synthesize_welcome", return_value=wav_fixture())
    def test_welcome_audio_uses_bilingual_parts(self, generate):
        started = self.start_conversation("reset", at_welcome=True)
        response = self.request_audio({"revision": started["revision"]}, token=self.token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, wav_fixture())
        generate.assert_called_once_with(WELCOME_PARTS)

    @patch("reception.views.synthesize", return_value=wav_fixture())
    def test_welcome_captions_use_the_same_language_and_text_as_each_voice_clip(self, generate):
        started = self.start_conversation("reset", at_welcome=True)
        self.assertEqual(started["reply"], " ".join(part["text"] for part in started["voice_parts"]))
        for index, (language, text) in enumerate(WELCOME_PARTS):
            payload = {"revision": started["revision"], "part": index, "voice_key": started["voice_key"]}
            self.assertEqual(self.request_audio(payload, token=self.token).status_code, 200)
            self.assertEqual(generate.call_args.args, (text, language))

    @patch("reception.views.synthesize")
    def test_english_page_voice_reads_the_arrival_prompt(self, generate):
        generate.return_value = wav_fixture()
        self.client.get("/?lang=en")
        started = self.client.post(
            reverse("reception-message"),
            {"language": "en", "start": True},
            content_type="application/json",
            headers={"X-CSRFToken": self.token},
        ).json()
        response = self.request_audio({"revision": started["revision"], "part": 0}, token=self.token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(started["reply"].startswith("Hi, this is Neptun Resort, Pucka 2 street"))
        self.assertIn("Specify the dates of your stay", started["reply"])
        generate.assert_called_once_with(ARRIVAL_PARTS["en"][0]["text"], "en")

    @patch("reception.views.synthesize", return_value=wav_fixture())
    def test_arrival_audio_serves_only_the_requested_segment(self, generate):
        endpoint = reverse("reception-message")
        headers = {"X-CSRFToken": self.token}
        for language, pauses in (("en", [(2, False), (5, True), (5, True)]), ("pl", [(5, True), (5, True)])):
            with self.subTest(language=language):
                started = self.client.post(
                    endpoint, {"language": language, "reset": True}, content_type="application/json", headers=headers,
                ).json()
                if language == "pl":
                    started = self.client.post(
                        endpoint, {"language": language, "revision": started["revision"], "timeout": True},
                        content_type="application/json", headers=headers,
                    ).json()
                parts = started["arrival_parts"]
                self.assertEqual([(part["seconds"], part["listen"]) for part in parts], pauses)
                for index, part in enumerate(ARRIVAL_PARTS[language]):
                    response = self.request_audio({"revision": started["revision"], "part": index}, token=self.token)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, wav_fixture())
                    self.assertEqual(generate.call_args.args, (part["text"], language))
                for invalid in (None, True, -1, len(parts), "0"):
                    response = self.request_audio({"revision": started["revision"], "part": invalid}, token=self.token)
                    self.assertEqual(response.status_code, 400)

    @patch("reception.views.synthesize")
    def test_arrival_segments_do_not_replace_unavailable_room_reply(self, generate):
        conversation = Conversation.objects.get()
        conversation.state = new_conversation("pl", "unavailable")
        conversation.save(update_fields=["state"])
        response = self.request_audio({"revision": conversation.state["revision"], "part": 0}, token=self.token)
        self.assertEqual(response.status_code, 400)
        generate.assert_not_called()

    @patch("reception.views.synthesize", return_value=wav_fixture())
    def test_edited_checkout_uses_separate_chat_and_voice_with_revision_bound_segments(self, generate):
        prompt, _ = ScenarioPrompt.objects.update_or_create(
            language="pl", key="check_out",
            defaults={
                "chat_text": "Wpisz datę wyjazdu.",
                "voice_parts": [
                    {"text": "Podaj datę wyjazdu.", "seconds": 3, "listen": False},
                    {"text": "Możesz też podać liczbę nocy.", "seconds": 0, "listen": False},
                ],
            },
        )
        payload = {"language": "pl", "revision": self.started["revision"], "message": "jutro"}
        response = self.client.post(
            reverse("reception-message"), payload, content_type="application/json",
            headers={"X-CSRFToken": self.token},
        )
        self.assertEqual(response.status_code, 200)
        turn = response.json()
        self.assertEqual(turn["reply"], "Wpisz datę wyjazdu.")
        self.assertEqual([part["seconds"] for part in turn["voice_parts"]], [3, 0])
        payload = {"revision": turn["revision"], "part": 0, "voice_key": turn["voice_key"]}
        self.assertEqual(self.request_audio(payload, token=self.token).status_code, 200)
        generate.assert_called_once_with("Podaj datę wyjazdu.", "pl")

        prompt.voice_parts[0]["seconds"] = 4
        prompt.save(update_fields=["voice_parts"])
        self.assertEqual(self.request_audio(payload, token=self.token).status_code, 409)
        generate.assert_called_once()

    @patch("reception.views.synthesize")
    def test_audio_uses_current_server_reply_and_requires_csrf(self, generate):
        generate.return_value = wav_fixture()
        self.assertEqual(self.request_audio(self.payload).status_code, 403)
        self.assertEqual(self.request_audio({**self.payload, "text": "arbitrary"}, token=self.token).status_code, 400)
        self.assertEqual(self.request_audio({"revision": "a" * 128}, token=self.token).status_code, 413)
        generate.assert_not_called()
        response = self.request_audio(self.payload, token=self.token)
        generate.assert_called_once_with(Conversation.objects.get().state["reply"], "pl")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/wav")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.content, wav_fixture())

    @patch("reception.views.synthesize")
    def test_revision_from_another_session_cannot_trigger_synthesis(self, generate):
        second_client = Client(enforce_csrf_checks=True)
        second_token = second_client.get("/").cookies["csrftoken"].value
        response = second_client.post(
            reverse("reception-audio"),
            self.payload,
            content_type="application/json",
            headers={"X-CSRFToken": second_token},
        )
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    @patch("reception.views.synthesize")
    def test_expired_session_and_stale_revision_cannot_trigger_synthesis(self, generate):
        self.assertEqual(self.request_audio({"revision": "a" * 32}, token=self.token).status_code, 409)
        Session.objects.filter(session_key=self.client.session.session_key).update(expire_date=timezone.now())
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 404)
        generate.assert_not_called()

    @patch("reception.views.synthesize")
    def test_changed_revision_is_rejected_with_or_without_audio(self, generate):
        def change_revision(*_):
            conversation = Conversation.objects.get(session_id=self.client.session.session_key)
            conversation.state["revision"] = "a" * 32
            conversation.save(update_fields=["state"])
            if generate.return_value is None:
                raise SpeechUnavailable
            return generate.return_value

        generate.side_effect = change_revision
        for result in (wav_fixture(), None):
            with self.subTest(audio_available=result is not None):
                started = self.start_conversation("reset")
                generate.return_value = result
                response = self.request_audio({"revision": started["revision"]}, token=self.token)
                self.assertEqual(response.status_code, 409)

    @patch("reception.views.synthesize", side_effect=SpeechUnavailable)
    def test_upstream_unavailable_keeps_conversation_current(self, generate):
        response = self.request_audio(self.payload, token=self.token)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Conversation.objects.get().state["revision"], self.payload["revision"])

    @patch("reception.views.synthesize", side_effect=SpeechBusy)
    def test_busy_is_retryable_and_never_overrides_a_new_revision(self, generate):
        before = Conversation.objects.get().state
        response = self.request_audio(self.payload, token=self.token)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"error": "speech_busy"})
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(Conversation.objects.get().state, before)

        def changed(*_):
            conversation = Conversation.objects.get()
            conversation.state["revision"] = "b" * 32
            conversation.save(update_fields=["state"])
            raise SpeechBusy

        generate.side_effect = changed
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 409)

    @patch("reception.speech.build_opener")
    def test_combined_speech_limit_preserves_a_web_thread_and_recovers(self, opener):
        for _ in range(3):
            self.assertTrue(speech_slots.acquire(blocking=False))
        try:
            for operation in (lambda: synthesize("Hello", "en"), lambda: transcribe(b"\0\0", "pl")):
                with self.assertRaises(SpeechBusy):
                    operation()
            opener.assert_not_called()
        finally:
            for _ in range(3):
                speech_slots.release()
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = wav_fixture()
        self.assertEqual(synthesize("Hello", "en"), wav_fixture())

    @patch("reception.speech.build_opener")
    def test_two_outbound_slots_and_runtime_busy_release_admission(self, opener):
        connection = opener.return_value.open
        connection.return_value.__enter__.return_value.read.return_value = wav_fixture()
        self.assertTrue(generation_slot.acquire(blocking=False))
        try:
            self.assertEqual(synthesize("Hello", "en"), wav_fixture())
            self.assertTrue(generation_slot.acquire(blocking=False))
            try:
                with self.assertRaises(SpeechBusy):
                    synthesize("Hello", "en")
            finally:
                generation_slot.release()
            connection.side_effect = HTTPError("http://piper", 429, "busy", {}, None)
            with self.assertRaises(SpeechBusy):
                synthesize("Hello", "en")
            connection.side_effect = None
            self.assertEqual(synthesize("Hello", "en"), wav_fixture())
        finally:
            generation_slot.release()

    @patch("reception.speech.build_opener")
    def test_runtime_request_is_bounded_and_sends_the_exact_piper_payload(self, opener):
        read = opener.return_value.open.return_value.__enter__.return_value.read
        read.return_value = wav_fixture()
        self.assertEqual(synthesize("Hello", "en"), wav_fixture())
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://piper:5000/synthesize")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"text": "Hello", "language": "en"})
        read.assert_called_with(MAX_AUDIO_BYTES + 1)
        read.return_value = b"not a WAV"
        with self.assertRaises(SpeechUnavailable):
            synthesize("Dzień dobry", "pl")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"text": "Dzień dobry", "language": "pl"})
        for invalid_audio in (
            wav_fixture(channels=2),
            wav_fixture(sample_width=1),
            wav_fixture(rate=44100),
            wav_fixture()[:-1],
            b"\0" * (MAX_AUDIO_BYTES + 1),
        ):
            read.return_value = invalid_audio
            with self.subTest(length=len(invalid_audio)), self.assertRaises(SpeechUnavailable):
                synthesize("Hello", "en")

    @override_settings(HOTEL_PIPER_URL="")
    def test_piper_is_optional(self):
        self.assertNotContains(self.client.get("/"), "data-audio-url")
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 503)

    @override_settings(HOTEL_PIPER_URL="broken")
    def test_invalid_runtime_url_falls_back(self):
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 503)
