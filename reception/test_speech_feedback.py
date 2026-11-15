from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from .models import CORRECTED_SAMPLE_AGE, SpeechTrainingSample
from .speech_feedback import corrected_booking_transcript


class SpeechFeedbackTests(TestCase):
    def sample(self, transcript, correction, *, stage="check_out", language="en", expired=False):
        sample = SpeechTrainingSample.objects.create(
            turn_key=uuid4().hex, stage=stage, language=language,
            transcript=transcript, corrected_transcript=correction, pcm=b"\0\0",
        )
        if expired:
            SpeechTrainingSample.objects.filter(pk=sample.pk).update(created_at=timezone.now() - CORRECTED_SAMPLE_AGE)
        return sample

    def checkout(self):
        return {"language": "en", "stage": "check_out", "check_in": timezone.localdate().isoformat()}

    def test_exact_reviewed_match_applies_only_in_same_stage_and_language(self):
        self.sample("a weak", "a week")
        self.sample("A Weak.", "A WEEK", stage="check_out")
        self.sample("a weak", "two nights", language="pl")
        self.sample("a weak", "tomorrow", stage="check_in")
        self.sample("a weak", "two nights", expired=True)
        self.sample("a weak", "")
        self.assertEqual(corrected_booking_transcript(self.checkout(), " A  WEAK! "), "a week")
        self.assertEqual(corrected_booking_transcript(self.checkout(), "a weak please"), "a weak please")

    def test_conflicting_reviews_leave_the_original_transcript(self):
        self.sample("a weak", "a week")
        self.sample("A WEAK", "two nights")
        self.assertEqual(corrected_booking_transcript(self.checkout(), "a weak"), "a weak")

    def test_correction_must_still_make_sense_for_this_booking(self):
        self.sample("too morrow", "tomorrow")
        later = (timezone.localdate() + timedelta(days=2)).isoformat()
        self.assertEqual(corrected_booking_transcript({**self.checkout(), "check_in": later}, "too morrow"), "too morrow")
        self.assertEqual(corrected_booking_transcript(self.checkout(), "too morrow"), "tomorrow")
        self.sample("next weak", "guest@example.test")
        self.assertEqual(corrected_booking_transcript(self.checkout(), "next weak"), "next weak")

    def test_valid_blank_and_sensitive_turns_skip_review_lookup(self):
        self.sample("a week", "two nights")
        self.sample("", "tomorrow")
        for state, text in (
            (self.checkout(), "a week"),
            (self.checkout(), ""),
            (self.checkout(), "502 503 504"),
            ({"language": "pl", "stage": "guests"}, "tree"),
            ({"language": "en", "stage": "confirm"}, "guess"),
            ({"language": "en", "stage": "phone"}, "some guest"),
            ({"language": "en", "stage": "email"}, "some guest"),
        ):
            with self.subTest(state=state, text=text), self.assertNumQueries(0):
                self.assertEqual(corrected_booking_transcript(state, text), text)
