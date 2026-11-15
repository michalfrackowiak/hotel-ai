from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Price, Room

from .models import ParserSample


@override_settings(HOTEL_PARSER_TRAINING_ENABLED=True, HOTEL_CONFIRMATION_WAIT_METHODS=())
class ParserReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        room = Room.objects.create(code="ROOM-2", capacity=2)
        Price.objects.create(room=room, nightly_rate=120)

    def request_turn(self, **payload):
        response = self.client.post(reverse("reception-message"), payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def start(self, language="en"):
        turn = self.request_turn(language=language, reset=True)
        if turn["stage"] == "welcome":
            turn = self.request_turn(language=language, revision=turn["revision"], timeout=True)
        return turn

    def answer(self, turn, text, **flags):
        return self.request_turn(language=turn["language"], revision=turn["revision"], message=text, **flags)

    def date_steps(self, language="en"):
        turn = self.start(language)
        turn = self.answer(turn, "tomorrow" if language == "en" else "jutro")
        return self.answer(turn, "for a week" if language == "en" else "na tydzień")

    def test_record_bilingual_chat_and_voice_with_extracted_values(self):
        en = self.start()
        en = self.answer(en, "tomorrow", voice=True)  # Typed while microphone mode is active.
        self.answer(en, "a weak", voice=True, transcript=True)
        pl = self.date_steps("pl")
        self.answer(pl, "dwie osoby")
        self.assertEqual(
            list(ParserSample.objects.order_by("pk").values_list("language", "stage", "source", "succeeded")),
            [
                ("en", "check_in", "chat", True),
                ("en", "check_out", "voice", False),
                ("pl", "check_in", "chat", True),
                ("pl", "check_out", "chat", True),
                ("pl", "guests", "chat", True),
            ],
        )
        self.assertEqual(ParserSample.objects.get(stage="guests").parsed_result, "2")
        self.assertEqual(ParserSample.objects.get(text="tomorrow").parsed_result,
                         (timezone.localdate() + timedelta(days=1)).isoformat())

    def test_reviewed_result_resolves_later_voice_error_but_not_chat(self):
        en = self.date_steps()
        retry = self.answer(en, "two guess", voice=True, transcript=True)
        self.assertEqual(retry["stage"], "guests")
        sample = ParserSample.objects.get(text="two guess")
        sample.corrected_result = "2"
        sample.save(update_fields=["corrected_result"])
        retry = self.answer(retry, "two guess")
        self.assertEqual(retry["stage"], "guests")
        offers = self.answer(retry, "two guess", voice=True, transcript=True)
        self.assertEqual(offers["stage"], "room")
        confirmation = self.answer(offers, "option 1")
        self.assertEqual(confirmation["stage"], "confirm")
        self.assertEqual(ParserSample.objects.get(stage="room").parsed_result, "ROOM-2")
        self.assertFalse(Booking.objects.exists())

    def test_records_contact_parses_and_expires_contact_corrections(self):
        turn = self.date_steps()
        for text in ("2", "1", "yes", "email"):
            turn = self.answer(turn, text)
        turn = self.answer(turn, "guest monkey example dot com", voice=True, transcript=True)
        sample = ParserSample.objects.get(stage="email")
        self.assertFalse(sample.succeeded)
        sample.corrected_result = "guest@example.com"
        sample.save(update_fields=["corrected_result"])
        receipt = self.answer(turn, "guest monkey example dot com", voice=True, transcript=True)
        self.assertEqual(receipt["stage"], "completed")
        self.assertEqual(Booking.objects.get().client.email, "guest@example.com")
        ParserSample.objects.filter(pk=sample.pk).update(created_at=timezone.now() - timedelta(days=31))
        call_command("expire_parser_samples", verbosity=0)
        self.assertFalse(ParserSample.objects.filter(pk=sample.pk).exists())

    @override_settings(HOTEL_PARSER_TRAINING_ENABLED=False)
    def test_disabled_tracking_uses_normal_parser(self):
        self.assertEqual(self.answer(self.start(), "tomorrow")["stage"], "check_out")
        self.assertFalse(ParserSample.objects.exists())

    def test_contact_details_entered_on_arrival_are_not_collected(self):
        turn = self.answer(self.start(), "Write me at guest@example.com")
        self.assertEqual(turn["stage"], "handover")
        self.assertFalse(ParserSample.objects.exists())
