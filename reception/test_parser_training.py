from datetime import date, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from bookings.models import Room

from .models import ParserSample
from .parser_training import learned_result, rule_key, validate_correction
from .parsing import StayDates


class ParserTrainingTests(TestCase):
    def sample(self, raw, corrected, *, stage, language="en", source="voice", age=0):
        item = ParserSample.objects.create(
            stage=stage, language=language, source=source, text=raw, corrected_result=corrected,
            succeeded=False, rule_key=rule_key(language, stage, raw),
        )
        if age:
            ParserSample.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=age))
        return item

    def test_relative_correction_is_scoped_and_uses_current_check_in(self):
        state = {"language": "en", "stage": "check_out", "check_in": "2027-01-10"}
        self.sample("A weak?", "a week", stage="check_out")
        self.sample("a weak", "two nights", stage="check_out", language="pl")
        self.sample("a weak", "three nights", stage="check_out", source="chat", age=181)
        self.sample("a weak", "tomorrow", stage="check_in")
        with patch("reception.parsing.timezone.localdate", return_value=date(2027, 1, 1)):
            self.assertEqual(learned_result(state, " a  Weak. ", None, source="voice"), date(2027, 1, 17))
            next_stay = {**state, "check_in": "2027-01-21"}
            self.assertEqual(learned_result(next_stay, "a weak", None, source="voice"), date(2027, 1, 28))
            self.assertIsNone(learned_result(state, "a weak", None, source="chat"))
            self.assertIsNone(learned_result(state, "a weak please", None, source="voice"))

    def test_consistent_review_can_correct_wrong_guess_but_direct_counts_remain_authoritative(self):
        state = {"language": "en", "stage": "guests"}
        self.sample("for guests", "four", stage="guests")
        self.assertEqual(learned_result(state, "For guests.", 2, source="voice"), 4)
        self.sample("for guests", "five", stage="guests")
        self.assertIsNone(learned_result(state, "for guests", 2, source="voice"))
        self.sample("two", "three", stage="guests")
        self.assertIsNone(learned_result(state, "two", 2, source="voice"))
        with self.assertRaisesMessage(ValidationError, "direct booking value"):
            validate_correction("guests", "two", "three", language="en", source="voice")

    def test_disabled_corrections_do_not_apply_or_conflict(self):
        state = {"language": "en", "stage": "guests"}
        raw = "tree people"
        first = self.sample(raw, "three", stage="guests")
        second = self.sample(raw, "four", stage="guests")
        self.assertTrue(first.correction_enabled)
        self.assertIsNone(learned_result(state, raw, None, source="voice"))
        ParserSample.objects.filter(pk=second.pk).update(correction_enabled=False)
        self.assertEqual(learned_result(state, raw, None, source="voice"), 3)
        ParserSample.objects.filter(pk=first.pk).update(correction_enabled=False)
        self.assertIsNone(learned_result(state, raw, None, source="voice"))
        ParserSample.objects.filter(pk=second.pk).update(correction_enabled=True)
        self.assertEqual(learned_result(state, raw, None, source="voice"), 4)

    def test_date_pair_correction_is_validated_at_review_and_against_current_time(self):
        state = {"language": "en", "stage": "check_in"}
        with patch("reception.parsing.timezone.localdate", return_value=date(2027, 1, 1)):
            raw = "january seventeenth to nineteenth"
            corrected = validate_correction("check_in", raw, "2027-01-17 / 2027-01-19")
            self.sample(raw, corrected, stage="check_in")
            self.assertEqual(
                learned_result(state, raw, date(2027, 1, 17), source="voice"),
                StayDates(date(2027, 1, 17), date(2027, 1, 19)),
            )
            with self.assertRaises(ValidationError):
                validate_correction("check_in", raw, "2027-01-19 / 2027-01-17")
        with patch("reception.parsing.timezone.localdate", return_value=date(2027, 1, 18)):
            self.assertIsNone(learned_result(state, raw, None, source="voice"))

    def test_room_correction_uses_only_rooms_quoted_for_this_turn(self):
        Room.objects.create(code="TEST-DOUBLE", capacity=2)
        choice = {"room": "TEST-DOUBLE", "room_id": 8}
        state = {"language": "en", "stage": "room", "choices": [choice]}
        raw = "double rum"
        corrected = validate_correction("room", raw, "room TEST-DOUBLE")
        self.sample(raw, corrected, stage="room")
        self.assertEqual(learned_result(state, raw, None, source="voice"), choice)
        self.assertIsNone(learned_result({**state, "choices": []}, raw, None, source="voice"))

    def test_contact_aliases_preserve_contact_and_are_rechecked_when_applied(self):
        email = "Guest monkey example dot test"
        phone = "plus sign 48 501 502 503"
        self.assertEqual(validate_correction("email", email, "guest@example.test"), "guest@example.test")
        self.assertEqual(validate_correction("phone", phone, "+48 501 502 503"), "+48501502503")
        for stage, raw, candidate in (
            ("email", email, "other@example.test"),
            ("email", "Guest@example.test", "guest@example.test"),
            ("phone", phone, "+48501502504"),
        ):
            with self.subTest(stage=stage, candidate=candidate), self.assertRaises(ValidationError):
                validate_correction(stage, raw, candidate)
        row = self.sample(email, "other@example.test", stage="email")
        state = {"language": "en", "stage": "email"}
        self.assertIsNone(learned_result(state, email, None, source="voice"))
        ParserSample.objects.filter(pk=row.pk).update(corrected_result="guest@example.test")
        self.assertEqual(learned_result(state, email, None, source="voice"), "guest@example.test")
        ParserSample.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=31))
        self.assertIsNone(learned_result(state, email, None, source="voice"))
