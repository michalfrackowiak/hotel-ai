from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking, Client

from .calls import DRAFT_LIFETIME, TERMINAL_LIFETIME, advance_call, expire_calls, start_call
from .models import PhoneCall


CALL_SID = "CA" + "a" * 32
OTHER_CALL_SID = "CA" + "b" * 32


class PhoneCallTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict("os.environ", HOTEL_DEMO_ADMIN_USERNAME="", HOTEL_DEMO_ADMIN_PASSWORD=""):
            call_command("seed_demo", stdout=StringIO())

    def turn(self, current, *, speech="", digit="", call_sid=CALL_SID):
        return advance_call(call_sid, current.revision, speech=speech, digit=digit)

    def prepare_quote(self, *, language="en", call_sid=CALL_SID):
        menu = start_call(call_sid)
        self.assertEqual(menu.stage, "language")
        if language == "en":
            inputs = ("Tomorrow.", "day after tomorrow", "two guests", "Jordan Example",
                      "Jordan DOT Example at example dot com.")
        else:
            inputs = ("Jutro。", "Pojutrze!", "dwie osoby", "Anna Kowalska",
                      "Anna kropka Kowalska MAŁPA example kropka com.")
        current = self.turn(menu, digit="1" if language == "en" else "2", call_sid=call_sid)
        self.assertEqual((current.language, current.stage), (language, "check_in"))
        for text, stage in zip(inputs, ("check_out", "guests", "name", "email", "confirm"), strict=True):
            current = self.turn(current, speech=text, call_sid=call_sid)
            self.assertEqual(current.stage, stage)
        return current

    def test_english_requires_explicit_confirmation_and_replays_same_receipt(self):
        quote = self.prepare_quote()
        self.assertIn("120.00 PLN", quote.reply)
        self.assertIn("Yes to book", quote.reply)
        self.assertFalse(Booking.objects.exists())
        ambiguous = self.turn(quote, speech="Yes, please")
        self.assertEqual(ambiguous.stage, "confirm")
        self.assertFalse(Client.objects.exists())
        self.assertEqual(self.turn(quote, speech="Yes!"), ambiguous)
        receipt = self.turn(ambiguous, speech="Yes!")
        booking = Booking.objects.get()
        self.assertEqual((receipt.stage, receipt.reference), ("completed", str(booking.reference)))
        self.assertEqual(booking.client.email, "Jordan.Example@example.com")
        self.assertEqual(self.turn(ambiguous, speech="Yes!"), receipt)
        self.assertEqual(start_call(CALL_SID), receipt)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertNotIn("name", PhoneCall.objects.get().state)
        self.assertNotIn("email", PhoneCall.objects.get().state)

    def test_polish_reuses_localized_quote_and_accepts_only_polish_confirmation(self):
        quote = self.prepare_quote(language="pl")
        self.assertIn("120,00 PLN", quote.reply)
        self.assertIn("Odpowiedz Tak", quote.reply)
        current = self.turn(quote, speech="Yes")
        self.assertEqual(current.stage, "confirm")
        self.assertFalse(Booking.objects.exists())
        receipt = self.turn(current, speech="Tak！")
        booking = Booking.objects.get()
        self.assertEqual(receipt.reference, str(booking.reference))
        self.assertIn("Numer rezerwacji", receipt.reply)
        self.assertEqual(booking.client.email, "Anna.Kowalska@example.com")

    def test_call_keys_and_revisions_cannot_advance_another_call(self):
        first = start_call(CALL_SID)
        other = start_call(OTHER_CALL_SID)
        self.assertEqual(self.turn(first, digit="1", call_sid=OTHER_CALL_SID), other)
        self.assertEqual(start_call("CA" + "A" * 32), first)
        self.assertEqual(advance_call("CA" + "c" * 32, first.revision, digit="1").stage, "ended")
        self.assertEqual(PhoneCall.objects.count(), 2)

    def test_three_invalid_menu_turns_end_the_call_without_counting_replay(self):
        start = start_call(CALL_SID)
        first = self.turn(start, digit="9")
        self.assertEqual(first.stage, "language")
        self.assertEqual(self.turn(start, digit="9"), first)
        second = self.turn(first)
        self.assertEqual(second.stage, "language")
        ended = self.turn(second, digit="9")
        self.assertEqual(ended.stage, "ended")
        self.assertEqual(start_call(CALL_SID), ended)
        self.assertFalse(Booking.objects.exists())

    def test_silence_after_quote_cannot_confirm_and_redacts_draft_on_third_miss(self):
        quote = self.prepare_quote(language="pl")
        first = self.turn(quote)
        self.assertIn("Nie usłyszałem", first.reply)
        self.assertEqual(self.turn(quote), first)
        second = self.turn(first, speech="x" * 501)
        self.assertEqual(first.reply, second.reply)
        ended = self.turn(second)
        self.assertEqual((ended.stage, ended.language), ("ended", "pl"))
        self.assertFalse(Booking.objects.exists())
        self.assertNotIn("email", PhoneCall.objects.get().state)

    def test_expiry_redacts_stale_draft_before_accepting_confirmation(self):
        quote = self.prepare_quote()
        now = timezone.now()
        with patch("telephony.calls.timezone.now", return_value=now + DRAFT_LIFETIME):
            expired = self.turn(quote, speech="Yes")
        self.assertEqual(expired.stage, "ended")
        self.assertFalse(Booking.objects.exists())
        self.assertNotIn("email", PhoneCall.objects.get().state)

    def test_expiry_command_redacts_drafts_and_prunes_terminal_receipts(self):
        quote = self.prepare_quote()
        other_quote = self.prepare_quote(call_sid=OTHER_CALL_SID)
        self.assertEqual(self.turn(other_quote, speech="Yes", call_sid=OTHER_CALL_SID).stage, "completed")
        now = timezone.now()
        with patch("telephony.calls.timezone.now", return_value=now + DRAFT_LIFETIME):
            output = StringIO()
            call_command("expire_phone_calls", stdout=output)
            self.assertIn("Redacted 1 expired draft(s)", output.getvalue())
            self.assertEqual(self.turn(quote, speech="Yes").stage, "ended")
            self.assertEqual(start_call(OTHER_CALL_SID).stage, "completed")
        with patch("telephony.calls.timezone.now", return_value=now + DRAFT_LIFETIME + TERMINAL_LIFETIME):
            self.assertEqual(expire_calls(), (0, 2))
        self.assertFalse(PhoneCall.objects.exists())
        self.assertEqual(Booking.objects.count(), 1)
