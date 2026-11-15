from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Conversation
from .speech import MAX_PCM_BYTES, MAX_TRANSCRIPT_BYTES, SpeechUnavailable, transcribe


@override_settings(HOTEL_ASR_URL="http://asr:5001")
class ReceptionTranscriptTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get("/?lang=pl").cookies["csrftoken"].value
        started = self.client.post(
            reverse("reception-message"), {"language": "pl", "start": True},
            content_type="application/json", headers={"X-CSRFToken": self.token},
        )
        arrival = self.client.post(
            reverse("reception-message"),
            {"language": "pl", "revision": started.json()["revision"], "timeout": True},
            content_type="application/json", headers={"X-CSRFToken": self.token},
        )
        self.revision = arrival.json()["revision"]
        self.pcm = b"\x10\x00" * 1600

    def upload(self, pcm=None, *, revision=None, csrf=True, content_type="application/octet-stream"):
        headers = {"X-Reception-Revision": revision or self.revision}
        if csrf:
            headers["X-CSRFToken"] = self.token
        return self.client.post(
            reverse("reception-transcript"), self.pcm if pcm is None else pcm,
            content_type=content_type, headers=headers,
        )

    @patch("reception.views.transcribe_welcome", return_value="English")
    def test_welcome_uses_bilingual_recognition(self, recognize):
        started = self.client.post(
            reverse("reception-message"), {"language": "pl", "reset": True},
            content_type="application/json", headers={"X-CSRFToken": self.token},
        )
        revision = started.json()["revision"]
        self.assertEqual(self.upload(revision=revision).json(), {"text": "English"})
        recognize.assert_called_once_with(self.pcm, "pl")

    @patch("reception.views.transcribe", return_value="Dwie osoby")
    def test_upload_requires_csrf_and_uses_current_session_language(self, recognize):
        self.assertEqual(self.upload(csrf=False).status_code, 403)
        self.assertEqual(self.upload(revision="a" * 32).status_code, 409)
        self.assertEqual(self.upload(content_type="audio/wav").status_code, 415)
        self.assertEqual(self.upload(b"\0").status_code, 400)
        self.assertEqual(self.upload(b"\0" * (MAX_PCM_BYTES + 2)).status_code, 413)
        recognize.assert_not_called()

        result = self.upload()
        self.assertEqual(result.json(), {"text": "Dwie osoby"})
        self.assertIn("no-store", result["Cache-Control"])
        recognize.assert_called_once_with(self.pcm, "pl")
        self.assertEqual(Conversation.objects.get().state["revision"], self.revision)

    @patch("reception.views.transcribe")
    def test_missing_session_and_completed_conversation_cannot_transcribe(self, recognize):
        other = Client(enforce_csrf_checks=True)
        token = other.get("/").cookies["csrftoken"].value
        self.assertEqual(other.post(
            reverse("reception-transcript"), self.pcm, content_type="application/octet-stream",
            headers={"X-CSRFToken": token, "X-Reception-Revision": self.revision},
        ).status_code, 404)
        conversation = Conversation.objects.get()
        conversation.state["stage"] = "completed"
        conversation.save(update_fields=["state"])
        self.assertEqual(self.upload().status_code, 409)
        recognize.assert_not_called()

    @patch("reception.views.transcribe")
    def test_empty_busy_and_stale_responses_keep_the_conversation_safe(self, recognize):
        recognize.return_value = ""
        self.assertEqual(self.upload().json(), {"text": ""})
        recognize.side_effect = SpeechUnavailable
        self.assertEqual(self.upload().status_code, 503)

        def change_revision(*_):
            conversation = Conversation.objects.get()
            conversation.state["revision"] = "b" * 32
            conversation.save(update_fields=["state"])
            return "Stale text"

        recognize.side_effect = change_revision
        self.assertEqual(self.upload().status_code, 409)

    @patch("reception.speech.build_opener")
    def test_runtime_proxy_sends_pcm_and_validates_bounded_json(self, opener):
        connection = opener.return_value.open
        read = connection.return_value.__enter__.return_value.read
        read.return_value = b'{"text":"Hello"}'
        self.assertEqual(transcribe(self.pcm, "en"), "Hello")
        request = connection.call_args.args[0]
        self.assertEqual(request.full_url, "http://asr:5001/transcribe?language=en")
        self.assertEqual(request.data, self.pcm)
        read.assert_called_with(MAX_TRANSCRIPT_BYTES + 1)
        for body in (b'{"text":12}', b"[]", b"x" * (MAX_TRANSCRIPT_BYTES + 1)):
            read.return_value = body
            with self.subTest(size=len(body)), self.assertRaises(SpeechUnavailable):
                transcribe(self.pcm, "pl")
