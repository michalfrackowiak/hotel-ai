from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from bookings.models import Booking, Client
from reception.models import ParserSample

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
            inputs = ("Tomorrow.", "for one night", "two guests", "one")
        else:
            inputs = ("Jutro。", "na jedną noc", "dwie osoby", "jeden")
        current = self.turn(menu, digit="2" if language == "en" else "", call_sid=call_sid)
        self.assertEqual((current.language, current.stage), (language, "check_in"))
        for text, stage in zip(inputs, ("check_out", "guests", "room", "confirm"), strict=True):
            current = self.turn(current, speech=text, call_sid=call_sid)
            self.assertEqual(current.stage, stage)
            if stage == "room":
                self.assertIn("120.00 PLN" if language == "en" else "120,00 PLN", current.reply)
        return current

    def test_english_requires_explicit_confirmation_and_replays_same_receipt(self):
        quote = self.prepare_quote()
        self.assertIn("You have selected room ROOM-2", quote.reply)
        self.assertFalse(Booking.objects.exists())
        ambiguous = self.turn(quote, speech="Yes, please")
        self.assertEqual(ambiguous.stage, "confirm")
        self.assertFalse(Client.objects.exists())
        self.assertEqual(self.turn(quote, speech="Yes!"), ambiguous)
        delivery = self.turn(ambiguous, speech="Yes!")
        self.assertEqual(delivery.stage, "delivery")
        self.assertTrue(delivery.reply.startswith("Your reservation has been tentatively created!"))
        self.assertFalse(Booking.objects.exists())
        email = self.turn(delivery, speech="email")
        self.assertEqual(email.stage, "email")
        pending = self.turn(email, speech="Jordan DOT Example at example dot com.")
        self.assertEqual(pending.stage, "contact_confirm")
        self.assertFalse(Client.objects.exists())
        receipt = self.turn(pending, speech="yes")
        booking = Booking.objects.get()
        self.assertEqual((receipt.stage, receipt.reference), ("completed", str(booking.reference)))
        self.assertIn("awaiting dispatch", receipt.reply)
        self.assertEqual(booking.client.email.casefold(), "jordan.example@example.com")
        self.assertEqual(self.turn(pending, speech="yes"), receipt)
        self.assertEqual(start_call(CALL_SID), receipt)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertNotIn("name", PhoneCall.objects.get().state)
        self.assertNotIn("email", PhoneCall.objects.get().state)

    def test_polish_reuses_localized_quote_and_accepts_only_polish_confirmation(self):
        quote = self.prepare_quote(language="pl")
        self.assertIn("Wybrałeś pokój ROOM-2", quote.reply)
        current = self.turn(quote, speech="Yes")
        self.assertEqual(current.stage, "confirm")
        self.assertFalse(Booking.objects.exists())
        delivery = self.turn(current, speech="Tak！")
        self.assertEqual(delivery.stage, "delivery")
        self.assertTrue(delivery.reply.startswith("Rezerwacja została wstępnie utworzona!"))
        phone = self.turn(delivery, speech="telefon")
        self.assertEqual(phone.stage, "phone")
        pending = self.turn(phone, speech="+48 500 000 123")
        self.assertEqual(pending.stage, "contact_confirm")
        self.assertFalse(Booking.objects.exists())
        receipt = self.turn(pending, speech="tak")
        booking = Booking.objects.get()
        self.assertEqual(receipt.reference, str(booking.reference))
        self.assertIn("Numer rezerwacji", receipt.reply)
        self.assertEqual(booking.client.phone, "+48500000123")

    def test_contact_silence_change_and_old_revision_do_not_create_booking(self):
        quote = self.prepare_quote(language="pl")
        delivery = self.turn(quote, speech="tak")
        email = self.turn(delivery, speech="email")
        first = self.turn(email, speech="gosk małpa example kropka com")
        self.assertEqual(first.stage, "contact_confirm")
        silent = self.turn(first)
        self.assertEqual(silent.stage, "contact_confirm")
        self.assertEqual(self.turn(first, speech="tak"), silent)
        entry = self.turn(silent, speech="nie")
        self.assertEqual(entry.stage, "email")
        second = self.turn(entry, speech="gosc małpa example kropka com")
        self.assertEqual(second.stage, "contact_confirm")
        self.assertEqual(self.turn(silent, speech="tak"), second)
        self.assertFalse(Client.objects.exists())
        self.assertFalse(Booking.objects.exists())
        handover = self.turn(second, speech="recepcja")
        self.assertEqual(handover.stage, "handover")
        self.assertNotIn("contact_candidate", PhoneCall.objects.get().state)
        self.assertFalse(Booking.objects.exists())

    def test_call_keys_and_revisions_cannot_advance_another_call(self):
        first = start_call(CALL_SID)
        other = start_call(OTHER_CALL_SID)
        self.assertEqual(self.turn(first, digit="1", call_sid=OTHER_CALL_SID), other)
        self.assertEqual(start_call("CA" + "A" * 32), first)
        self.assertEqual(advance_call("CA" + "c" * 32, first.revision, digit="1").stage, "ended")
        self.assertEqual(PhoneCall.objects.count(), 2)

    @override_settings(HOTEL_PARSER_TRAINING_ENABLED=True)
    def test_phone_turns_record_voice_input_once_and_use_a_reviewed_rule(self):
        arrival = self.turn(start_call(CALL_SID), digit="2")
        checkout = self.turn(arrival, speech="Tomorrow.")
        retry = self.turn(checkout, speech="A weak.")
        self.assertEqual(retry.stage, "check_out")
        sample = ParserSample.objects.get(stage="check_out")
        self.assertEqual((sample.language, sample.source, sample.succeeded), ("en", "voice", False))
        sample.corrected_result = "a week"
        sample.save(update_fields=["corrected_result"])
        self.assertEqual(self.turn(checkout, speech="A weak."), retry)
        self.assertEqual(ParserSample.objects.filter(stage="check_out").count(), 1)
        self.assertEqual(self.turn(retry, speech="A weak.").stage, "guests")

    def test_menu_defaults_to_polish_and_check_in_silence_requests_handover(self):
        start = start_call(CALL_SID)
        first = self.turn(start, digit="9")
        self.assertEqual((first.stage, first.language), ("check_in", "pl"))
        self.assertEqual(self.turn(start, digit="9"), first)
        second = self.turn(first)
        self.assertEqual(second.stage, "handover")
        self.assertEqual(start_call(CALL_SID), second)
        self.assertFalse(Booking.objects.exists())

    def test_english_by_speech_and_polish_date_in_menu(self):
        result = self.turn(start_call(CALL_SID), speech="English.")
        self.assertEqual((result.stage, result.language), ("check_in", "en"))
        result = self.turn(start_call(OTHER_CALL_SID), speech="Jutro.", call_sid=OTHER_CALL_SID)
        self.assertEqual((result.stage, result.language), ("check_out", "pl"))

    @patch("django.utils.timezone.localdate", return_value=date(2027, 12, 30))
    def test_noisy_stay_is_understood_at_arrival_and_in_the_polish_menu(self, _today):
        english = self.turn(start_call(CALL_SID), digit="2")
        english = self.turn(english, speech="strike of will be the from tomorrow for three days")
        self.assertEqual((english.language, english.stage), ("en", "guests"))
        self.assertIn("Check-in is 2027-12-31 and check-out is 2028-01-03.", english.reply)

        polish = self.turn(start_call(OTHER_CALL_SID), speech="trzask od jutra przez trzy dni", call_sid=OTHER_CALL_SID)
        self.assertEqual((polish.language, polish.stage), ("pl", "guests"))
        self.assertIn("Przyjazd 31.12.2027, wyjazd 03.01.2028.", polish.reply)
        self.assertFalse(Booking.objects.exists())

    def test_explicit_cancellation_ends_the_call(self):
        quote = self.prepare_quote(language="pl")
        cancelled = self.turn(quote, speech="Rezygnuję.")
        self.assertEqual(cancelled.stage, "cancelled")
        self.assertEqual(self.turn(quote, speech="Tak"), cancelled)
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
        delivery = self.turn(other_quote, speech="Yes", call_sid=OTHER_CALL_SID)
        email = self.turn(delivery, speech="email", call_sid=OTHER_CALL_SID)
        self.assertEqual(
            self.turn(email, speech="other@example.test", call_sid=OTHER_CALL_SID).stage, "contact_confirm",
        )
        pending = start_call(OTHER_CALL_SID)
        self.assertEqual(self.turn(pending, speech="yes", call_sid=OTHER_CALL_SID).stage, "completed")
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
