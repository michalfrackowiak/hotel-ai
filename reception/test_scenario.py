import io
import wave
from datetime import date
from unittest.mock import call, patch

from django.test import SimpleTestCase, TestCase

from bookings.models import Booking, Price, Room

from .contacts import parse_email, parse_phone
from .conversation import WELCOME_PARTS, advance, new_conversation
from .parsing import parse_stay_input
from .speech import SpeechUnavailable, synthesize_welcome, transcribe_welcome


class ScenarioInputTests(SimpleTestCase):
    @patch("reception.parsing.timezone.localdate", return_value=date(2026, 11, 15))
    def test_yearless_ordinals_and_month_rollover(self, _today):
        for language, text, expected in (
            ("pl", "za trzy dni", date(2026, 11, 18)),
            ("en", "the day after tomorrow", date(2026, 11, 17)),
            ("pl", "12.07", date(2027, 7, 12)),
            ("pl", "trzydziestego pierwszego grudnia", date(2026, 12, 31)),
            ("en", "June 10th", date(2027, 6, 10)),
            ("en", "November twenty-first", date(2026, 11, 21)),
            ("en", "the twenty-first of November", date(2026, 11, 21)),
            ("en", "22nd", date(2026, 11, 22)),
            ("pl", "dziesiątego czerwca 2026 roku", date(2026, 6, 10)),
            ("en", "February 30th", None),
            ("pl", "21 czerwca maja", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_in", text, language), expected)

    @patch("reception.parsing.timezone.localdate", return_value=date(2026, 11, 15))
    def test_duration_uses_arrival_and_relative_dates_use_today(self, _today):
        arrival = date(2026, 12, 29)
        for language, text, expected in (
            ("en", "for 3 nights", date(2027, 1, 1)),
            ("en", "3", date(2027, 1, 1)),
            ("en", "three", date(2027, 1, 1)),
            ("pl", "na trzy noce", date(2027, 1, 1)),
            ("pl", "trzy", date(2027, 1, 1)),
            ("pl", "trzeciego", date(2026, 11, 3)),
            ("en", "3rd", date(2026, 11, 3)),
            ("en", "for a week", date(2027, 1, 5)),
            ("pl", "na tydzień", date(2027, 1, 5)),
            ("pl", "przez 5 dni", date(2027, 1, 3)),
            ("en", "in three days", date(2026, 11, 18)),
            ("en", "January 2nd", date(2027, 1, 2)),
            ("en", "for 0 nights", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_stay_input("check_out", text, language, check_in=arrival), expected)
        self.assertEqual(
            parse_stay_input("check_out", "for a month", "en", check_in=date(2027, 1, 31)),
            date(2027, 2, 28),
        )

    def test_spoken_contact_formats(self):
        self.assertEqual(parse_email("g o s c małpa example kropka com."), "gosc@example.com")
        self.assertEqual(parse_email("j a n e at example dot com."), "jane@example.com")
        self.assertEqual(parse_email("Guest at example dot com."), "guest@example.com")
        self.assertEqual(parse_email("Guest plus one at example dot com."), "guest+1@example.com")
        self.assertIsNone(parse_email("another question"))
        self.assertEqual(parse_phone("plus cztery osiem 501 dwa trzy cztery 567"), "+48501234567")
        self.assertIsNone(parse_phone("today 501 234 567"))

    @patch("reception.speech.transcribe")
    def test_welcome_uses_english_recognizer_for_the_language_command(self, transcribe):
        samples = b"\x01\x00"
        transcribe.side_effect = ["głos", "english", "jutro", "you throw", "pojutrze", SpeechUnavailable()]
        self.assertEqual(transcribe_welcome(samples, "pl"), "English")
        self.assertEqual(transcribe_welcome(samples, "pl"), "jutro")
        self.assertEqual(transcribe_welcome(samples, "pl"), "pojutrze")
        self.assertEqual(transcribe.call_args_list, [call(samples, "pl"), call(samples, "en")] * 3)

    @patch("reception.speech.synthesize")
    def test_welcome_audio_keeps_the_language_and_order_of_each_sentence(self, synthesize):
        self.addCleanup(synthesize_welcome.cache_clear)

        def segment(sample):
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                wav.writeframes(bytes((sample, 0)))
            return output.getvalue()

        synthesize.side_effect = [segment(1), segment(2), segment(3)]
        with wave.open(io.BytesIO(synthesize_welcome(WELCOME_PARTS)), "rb") as wav:
            self.assertEqual(wav.getframerate(), 22050)
            self.assertEqual(wav.readframes(3), b"\x01\x00\x02\x00\x03\x00")
        self.assertEqual(synthesize.call_args_list, [call(text, language) for language, text in WELCOME_PARTS])


class ScenarioConversationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for number in range(1, 4):
            room = Room.objects.create(code=f"R{number}", capacity=6)
            Price.objects.create(room=room, nightly_rate=100 * number)

    def turn(self, state, text, *, voice=False):
        check_in = date.fromisoformat(state["check_in"]) if "check_in" in state else None
        stage = "check_in" if state["stage"] == "welcome" else state["stage"]
        parsed = parse_stay_input(stage, text, state["language"], check_in=check_in)
        return advance(state, text, parsed, voice=voice)

    def choose_room(self, *, voice=False):
        state = self.turn(new_conversation("pl"), "English", voice=voice)
        self.assertEqual(state["language"], "en")
        for message in ("tomorrow", "for 3 nights", "two people"):
            state = self.turn(state, message, voice=voice)
        return state

    def test_voice_limits_room_list_but_chat_can_choose_any_available_room(self):
        voice = self.choose_room(voice=True)
        self.assertEqual(voice["stage"], "room")
        self.assertEqual(len(voice["choices"]), 2)
        self.assertEqual(self.turn(voice, "3", voice=True)["stage"], "room")
        chat = self.choose_room()
        self.assertIn("room R3 for 900.00 PLN total", chat["reply"])
        state = self.turn(chat, "option three")
        self.assertEqual(state["stage"], "confirm")
        self.assertTrue(state["reply"].startswith("You have selected room R3 from "))
        self.assertEqual(self.turn(state, "cancel")["stage"], "cancelled")
        self.assertFalse(Booking.objects.exists())

    def test_option_numbers_take_priority_over_numeric_room_codes(self):
        Room.objects.filter(code="R1").update(code="2")
        state = self.choose_room(voice=True)
        self.assertEqual(self.turn(state, "2", voice=True)["room"], "R2")
        self.assertEqual(self.turn(state, "room 2", voice=True)["room"], "2")

    def test_confirmation_requires_consent_then_valid_contact(self):
        state = self.turn(self.choose_room(), "R2")
        self.assertEqual(self.turn(state, "yes, maybe")["stage"], "confirm")
        state = self.turn(state, "confirm")
        self.assertEqual(state["stage"], "delivery")
        self.assertTrue(state["reply"].startswith("Your reservation has been tentatively created!"))
        self.assertIn("booking_key", state)
        state = self.turn(state, "phone")
        self.assertEqual(state["stage"], "phone")
        self.assertEqual(self.turn(state, "123")["stage"], "phone")
        self.assertFalse(Booking.objects.exists())
        receipt = self.turn(state, "501 234 567")
        self.assertEqual(receipt["stage"], "completed")
        self.assertEqual(self.turn(receipt, "501 234 567"), receipt)
        booking = Booking.objects.get()
        self.assertEqual(booking.room.code, "R2")
        self.assertEqual(booking.client.phone, "+48501234567")
        self.assertEqual(str(booking.reference), receipt["reference"])
        self.assertNotIn("501234567", receipt["reply"])
        self.assertNotIn("501 234 567", receipt["reply"])

    def test_handover_on_unrelated_or_silent_arrival_turn(self):
        for message in ("I need to speak to reception", ""):
            state = self.turn(new_conversation("en", "check_in"), message)
            self.assertEqual(state["stage"], "handover")
            self.assertEqual(self.turn(state, "tomorrow"), state)
        self.assertFalse(Booking.objects.exists())

    def test_welcome_waits_separately_from_check_in(self):
        welcome = new_conversation("pl")
        self.assertEqual(welcome["stage"], "welcome")
        self.assertNotIn("termin przyjazdu", welcome["reply"])
        after_silence = self.turn(welcome, "")
        self.assertEqual((after_silence["stage"], after_silence["language"]), ("check_in", "pl"))
        self.assertIn("planowany termin przyjazdu", after_silence["reply"])
        after_english = self.turn(welcome, "2")
        self.assertEqual((after_english["stage"], after_english["language"]), ("check_in", "en"))
        self.assertIn("planned arrival date", after_english["reply"])
        after_early_date = self.turn(welcome, "jutro")
        self.assertEqual(after_early_date["stage"], "check_out")
