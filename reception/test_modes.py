import json

from django.test import Client, TestCase
from django.urls import reverse

from .models import Conversation, StaffHandover


class ReceptionModeTests(TestCase):
    def post(self, *, client=None, language="en", **payload):
        client = self.client if client is None else client
        return client.post(
            reverse("reception-message"), json.dumps({"language": language, **payload}),
            content_type="application/json",
        )

    def test_chat_and_voice_have_distinct_bilingual_controls_and_links(self):
        for language, chat_title, voice_title in (
            ("en", "Reception chat", "Voice booking"),
            ("pl", "Czat z recepcją", "Rozmowa głosowa"),
        ):
            with self.subTest(language=language):
                chat = self.client.get(reverse("reception-chat"), {"lang": language})
                voice = self.client.get(reverse("reception-voice"), {"lang": language})
                self.assertContains(chat, f'<html lang="{language}">')
                self.assertContains(voice, f'<html lang="{language}">')
                self.assertContains(chat, chat_title)
                self.assertContains(voice, voice_title)
                self.assertContains(chat, f'/voice/?lang={language}&amp;continue=1')
                self.assertContains(voice, f'/chat/?lang={language}&amp;continue=1')
                self.assertContains(chat, '/chat/?lang=en')
                self.assertContains(voice, '/voice/?lang=pl')
                for marker in ("start-voice", "reception/voice.js", "chat-type-instead"):
                    self.assertNotContains(chat, marker)
                    self.assertContains(voice, marker)
                for page in (chat, voice):
                    self.assertContains(page, 'id="chat-log"')
                    self.assertContains(page, 'id="chat-form"')
        self.assertFalse(Conversation.objects.exists())

    def test_new_drafts_capture_channel_and_switches_keep_stay_and_revision(self):
        for language, message in (("en", "tomorrow for three nights"), ("pl", "od jutra na trzy noce")):
            for channel, other in (("voice", "chat"), ("chat", "voice")):
                with self.subTest(language=language, entry=channel):
                    client = Client()
                    initial = self.post(client=client, language=language, start=True, resume=True, **(
                        {"voice": True} if channel == "voice" else {}
                    )).json()
                    self.assertEqual(initial["stage"], "welcome" if language == "pl" else "check_in")
                    advance = self.post(client=client, language=language, message=message, revision=initial["revision"])
                    self.assertEqual(advance.json()["stage"], "guests")
                    conversation = Conversation.objects.get(session_id=client.session.session_key)
                    saved = conversation.state

                    destination = client.get(reverse(f"reception-{other}"), {
                        "continue": "1", "lang": "pl" if language == "en" else "en",
                    })
                    self.assertContains(destination, f'<html lang="{language}">')
                    self.assertContains(destination, "data-resume")
                    resumed = self.post(client=client, language="pl" if language == "en" else "en",
                                        start=True, resume=True, **({"voice": True} if other == "voice" else {}))
                    self.assertEqual(resumed.json()["revision"], advance.json()["revision"])
                    self.assertEqual(resumed.json()["language"], language)
                    self.assertEqual(resumed.json()["stage"], "guests")
                    conversation.refresh_from_db()
                    self.assertEqual(conversation.state, saved)
                    self.assertEqual(conversation.state["scenario_pin"]["channel"], channel)
                    self.assertLess(saved["check_in"], saved["check_out"])

    def test_resuming_an_empty_handover_keeps_the_open_staff_request(self):
        first = self.post(start=True).json()
        handover = self.post(timeout=True, revision=first["revision"]).json()
        self.assertEqual(handover["stage"], "handover")
        resumed = self.post(start=True, voice=True, resume=True).json()
        self.assertEqual(resumed["stage"], "handover")
        self.assertEqual(resumed["revision"], handover["revision"])
        handover_record = StaffHandover.objects.get()
        self.assertFalse(handover_record.closed)
        self.assertFalse(handover_record.messages.exists())

    def test_explicit_language_change_is_distinct_from_mode_switch(self):
        self.post(start=True, voice=True)
        page = self.client.get(reverse("reception-voice"), {"lang": "pl"})
        self.assertContains(page, '<html lang="pl">')
        self.assertNotContains(page, "data-resume")
        changed = self.post(language="pl", start=True, voice=True).json()
        self.assertEqual(changed["stage"], "welcome")
        self.assertEqual(changed["language"], "pl")
        self.assertEqual(Conversation.objects.get().state["scenario_pin"]["channel"], "voice")

    def test_resume_is_only_allowed_for_start(self):
        self.assertEqual(self.post(start=True, resume=False).status_code, 400)
        self.assertEqual(self.post(reset=True, resume=True).status_code, 400)
