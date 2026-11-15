from datetime import timedelta
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from bookings.models import Price, Room

from .conversation import ARRIVAL_PARTS, advance, new_conversation
from .models import ScenarioPrompt
from .parsing import StayDates
from .receipt import start_receipt
from .scenario import default_chat_text, render_chat, scenario_overrides, voice_segments


class ScenarioRenderingTests(TestCase):
    def edit(self, language, key, **fields):
        return ScenarioPrompt.objects.update_or_create(language=language, key=key, defaults=fields)[0]

    def test_legacy_arrival_retains_listening_windows_and_can_render_new_copy(self):
        state = {"language": "en", "stage": "check_in", "reply": default_chat_text("en", "web_check_in")}
        self.assertEqual(render_chat(state), state["reply"])
        self.assertEqual(voice_segments(state), [{**part, "language": "en"} for part in ARRIVAL_PARTS["en"]])
        self.edit("en", "web_check_in", chat_text="What dates would you like?")
        self.assertEqual(render_chat(state), "What dates would you like?")
        self.assertEqual(voice_segments(state)[-1]["seconds"], 5)

    def test_one_welcome_edit_preserves_other_languages_and_voice_order(self):
        state = new_conversation("pl")
        self.assertEqual(voice_segments(state), [])
        self.edit(
            "en", "welcome_english_option", chat_text="Enter English to continue in English.",
            voice_parts=[{"text": "Say English now.", "seconds": 1, "listen": False}],
        )
        overrides = scenario_overrides(state)
        with self.assertNumQueries(0):
            self.assertIn("Enter English to continue in English.", render_chat(state, overrides=overrides))
            parts = voice_segments(state, overrides=overrides)
        self.assertEqual([part["language"] for part in parts], ["pl", "en", "pl"])
        self.assertEqual(parts[1], {"text": "Say English now.", "seconds": 1, "listen": False, "language": "en"})

    def test_rooms_render_selected_format_without_altering_prices_or_choice(self):
        for code, rate in (("TEST-A", 120), ("TEST-B", 150)):
            Price.objects.create(room=Room.objects.create(code=code, capacity=2), nightly_rate=rate)
        arrival = timezone.localdate()
        departure = arrival + timedelta(days=2)
        state = advance(new_conversation("en", "check_in"), "today for two nights", StayDates(arrival, departure))
        self.edit("en", "guests", chat_text="How many people will visit?")
        self.assertIn("How many people will visit?", render_chat(state))
        self.assertIn(str(arrival), render_chat(state))

        state = advance(state, "two", 2)
        self.edit(
            "en", "option", chat_text="{room} costs {total} PLN",
            voice_parts=[{"text": "Choice {number}: {room} costs {total} PLN", "seconds": 0, "listen": False}],
        )
        self.assertIn("TEST-B costs 300.00 PLN", render_chat(state))
        self.assertIn("Choice 2: TEST-B costs 300.00 PLN", voice_segments(state)[0]["text"])
        selected = advance(state, "2", None)
        self.assertEqual((selected["stage"], selected["room"], selected["total_price"]), ("confirm", "TEST-B", "300.00"))

    @override_settings(HOTEL_CONFIRMATION_WAIT_METHODS=(), HOTEL_SMTP_HOST="", HOTEL_TWILIO_ACCOUNT_SID="")
    def test_receipt_edit_keeps_reference_and_does_not_change_default_reply(self):
        reference = str(uuid4())
        state = start_receipt({"language": "en", "flow": "neptun", "revision": uuid4().hex}, reference, "email")
        self.edit(
            "en", "receipt_reference", chat_text="Keep booking code {reference}.",
            voice_parts=[{"text": "Your booking code is {reference}.", "seconds": 2, "listen": False}],
        )
        self.assertEqual(state["prompt_key"], "receipt_pending")
        self.assertIn(f"Your reference is {reference}.", state["reply"])
        self.assertIn(f"Keep booking code {reference}.", render_chat(state))
        self.assertEqual(voice_segments(state)[-1]["text"], f"Your booking code is {reference}.")
