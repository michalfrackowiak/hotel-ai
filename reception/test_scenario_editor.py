from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .models import ScenarioPrompt
from .scenario import scenario_keys


class ScenarioEditorTests(TestCase):
    def setUp(self):
        self.prompt = ScenarioPrompt.objects.get(language="en", key="check_out")
        self.path = reverse("admin:reception_scenarioprompt_change", args=[self.prompt.pk])

    def login(self, permission):
        user = get_user_model().objects.create_user(username=permission, is_staff=True)
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label="reception", codename=permission,
        ))
        self.client.force_login(user)

    def test_catalog_is_seeded_and_viewers_cannot_save(self):
        for language in ("en", "pl"):
            keys = ScenarioPrompt.objects.filter(language=language).values_list("key", flat=True)
            self.assertCountEqual(keys, scenario_keys(language))
        self.assertEqual(self.client.get(self.path).status_code, 302)
        self.login("view_scenarioprompt")
        self.assertContains(self.client.get(self.path), "Chat text")
        self.assertEqual(self.client.post(self.path, {"chat_text": "New text"}).status_code, 403)

    def test_staff_can_edit_separate_text_and_pauses_and_restore_defaults(self):
        self.login("change_scenarioprompt")
        self.assertContains(self.client.get(self.path), "Please enter your departure date")
        response = self.client.post(self.path, {
            "chat_text": "Type the departure date.",
            "voice_parts_1_text": "Say the date you leave.",
            "voice_parts_1_seconds": "3",
            "voice_parts_2_text": "You can also say how many nights.",
            "voice_parts_2_seconds": "0",
            "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.chat_text, "Type the departure date.")
        self.assertEqual([part["seconds"] for part in self.prompt.voice_parts], [3, 0])
        response = self.client.post(self.path, {"chat_text": "", "reset_voice": "on", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        self.prompt.refresh_from_db()
        self.assertEqual((self.prompt.chat_text, self.prompt.voice_parts), ("", []))

    def test_editor_rejects_missing_quote_fields_and_an_invalid_pause(self):
        self.login("change_scenarioprompt")
        prompt = ScenarioPrompt.objects.get(language="en", key="confirm")
        response = self.client.post(reverse("admin:reception_scenarioprompt_change", args=[prompt.pk]), {
            "chat_text": "Confirm now",
            "voice_parts_1_text": "Your room {room}, from {check_in} to {check_out}, for {guests}.",
            "voice_parts_1_seconds": "31",
            "_save": "Save",
        })
        self.assertContains(response, "Keep these placeholders")
        self.assertContains(response, "must be a whole number from 0 to 30 seconds")
        prompt.refresh_from_db()
        self.assertEqual((prompt.chat_text, prompt.voice_parts), ("", []))
