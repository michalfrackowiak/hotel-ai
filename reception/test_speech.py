import io
import json
import wave
from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Conversation
from .speech import MAX_AUDIO_BYTES, SpeechUnavailable, synthesize


def wav_fixture():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 16)
    return output.getvalue()


@override_settings(HOTEL_ZONOS_URL="http://zonos:1919")
class ReceptionAudioTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get("/?lang=pl").cookies["csrftoken"].value
        self.started = self.client.post(reverse("reception-message"), {"language": "pl", "start": True},
                                        content_type="application/json", headers={"X-CSRFToken": self.token}).json()
        self.payload = {"revision": self.started["revision"]}

    def request_audio(self, payload, *, token=None):
        headers = {} if token is None else {"X-CSRFToken": token}
        return self.client.post(reverse("reception-audio"), payload, content_type="application/json", headers=headers)

    @patch("reception.views.synthesize")
    def test_audio_uses_current_server_reply_and_requires_csrf(self, generate):
        generate.return_value = wav_fixture()
        self.assertEqual(self.request_audio(self.payload).status_code, 403)
        self.assertEqual(self.request_audio({**self.payload, "text": "arbitrary"}, token=self.token).status_code, 400)
        generate.assert_not_called()
        response = self.request_audio(self.payload, token=self.token)
        generate.assert_called_once_with(self.started["reply"], "pl")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/wav")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.content, wav_fixture())

    @patch("reception.views.synthesize")
    def test_expired_session_and_stale_revision_cannot_trigger_synthesis(self, generate):
        self.assertEqual(self.request_audio({"revision": "a" * 32}, token=self.token).status_code, 409)
        Session.objects.filter(session_key=self.client.session.session_key).update(expire_date=timezone.now())
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 404)
        generate.assert_not_called()

    @patch("reception.views.synthesize")
    def test_changed_revision_is_rejected_even_if_synthesis_fails(self, generate):
        def change_revision(*_):
            conversation = Conversation.objects.get(session_id=self.client.session.session_key)
            conversation.state["revision"] = "a" * 32
            conversation.save(update_fields=["state"])
            raise SpeechUnavailable

        generate.side_effect = change_revision
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 409)

    @patch("reception.views.synthesize", side_effect=SpeechUnavailable)
    def test_upstream_unavailable_keeps_conversation_current(self, generate):
        response = self.request_audio(self.payload, token=self.token)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Conversation.objects.get().state["revision"], self.payload["revision"])

    @patch("reception.speech.build_opener")
    def test_runtime_request_is_bounded_and_uses_zonos_language_mapping(self, opener):
        read = opener.return_value.open.return_value.__enter__.return_value.read
        read.return_value = wav_fixture()
        self.assertEqual(synthesize("Hello", "en"), wav_fixture())
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://zonos:1919/v2/generate")
        self.assertEqual(json.loads(request.data)["language"], "en_us")
        read.assert_called_with(MAX_AUDIO_BYTES + 1)
        read.return_value = b"not a WAV"
        with self.assertRaises(SpeechUnavailable):
            synthesize("Dzień dobry", "pl")
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)["language"], "pl")
        read.return_value = wav_fixture()[:-1]
        with self.assertRaises(SpeechUnavailable):
            synthesize("Hello", "en")

    @override_settings(HOTEL_ZONOS_URL="broken")
    def test_invalid_runtime_url_falls_back(self):
        self.assertEqual(self.request_audio(self.payload, token=self.token).status_code, 503)
