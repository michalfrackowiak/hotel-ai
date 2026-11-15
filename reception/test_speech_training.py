import io
import json
import wave
from datetime import timedelta
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SpeechTrainingSample
from .speech import MAX_PCM_BYTES
from .speech_training import record_failed_transcript


PCM = b"\x05\x00" * 6400


def record(index, **fields):
    return record_failed_transcript(**{
        "turn_key": f"{index:064x}", "language": "en", "stage": "check_in", "pcm": PCM,
        "transcript": "the day before lawn",
        **fields,
    })


class SpeechTrainingRecordsTests(TestCase):
    def test_collects_only_bounded_non_contact_booking_samples(self):
        for fields in (
            {"stage": "email"},
            {"stage": "phone"},
            {"stage": "handover"},
            {"turn_key": "session-key"},
            {"language": "de"},
            {"pcm": b"\0"},
            {"pcm": b""},
            {"pcm": b"\0" * (MAX_PCM_BYTES + 2)},
            {"transcript": "a" * 501},
            {"transcript": "guest@example.test"},
            {"transcript": "guest at example dot test"},
            {"transcript": "call me on plus four eight five zero seven two three four five six seven"},
            {"transcript": "call me at +48 507 234 567"},
        ):
            with self.subTest(fields=fields.keys()):
                self.assertFalse(record(1, **fields))
        self.assertFalse(SpeechTrainingSample.objects.exists())
        self.assertTrue(record(1, transcript=""))
        self.assertTrue(record(2, transcript="could we check in 2027-11-05 please"))
        self.assertFalse(record(1, transcript="duplicate"))
        self.assertEqual(SpeechTrainingSample.objects.count(), 2)
        first = SpeechTrainingSample.objects.get(turn_key=f"{1:064x}")
        self.assertEqual(first.pcm, PCM)
        self.assertEqual(first.transcript, "")

    @patch("reception.speech_training.MAX_TRAINING_SAMPLES", 1)
    def test_intake_prunes_expired_rows_and_respects_storage_cap(self):
        self.assertTrue(record(1))
        self.assertFalse(record(2))
        SpeechTrainingSample.objects.update(created_at=timezone.now() - timedelta(days=31))
        self.assertTrue(record(2))
        self.assertEqual(SpeechTrainingSample.objects.get().turn_key, f"{2:064x}")

    def test_cleanup_expires_uncorrected_and_corrected_samples_at_different_ages(self):
        now = timezone.now()
        for index in (1, 2, 3):
            self.assertTrue(record(index))
        for index, age, correction in ((1, 31, ""), (2, 31, "Tomorrow"), (3, 181, "Tomorrow")):
            SpeechTrainingSample.objects.filter(turn_key=f"{index:064x}").update(
                created_at=now - timedelta(days=age), corrected_transcript=correction,
            )
        output = io.StringIO()
        call_command("expire_speech_training", stdout=output)
        self.assertIn("Removed 2 expired speech training sample(s).", output.getvalue())
        self.assertEqual(SpeechTrainingSample.objects.get().turn_key, f"{2:064x}")


class SpeechTrainingAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        record(1)
        cls.sample = SpeechTrainingSample.objects.get()
        cls.staff = get_user_model().objects.create_user(username="speech-reviewer", is_staff=True)

    def setUp(self):
        self.change = reverse("admin:reception_speechtrainingsample_change", args=(self.sample.pk,))
        self.audio = reverse("admin:reception_speechtrainingsample_audio", args=(self.sample.pk,))
        self.changelist = reverse("admin:reception_speechtrainingsample_changelist")
        self.export = {
            "action": "export_corrected", "_selected_action": str(self.sample.pk), "index": "0",
        }

    def grant(self, *actions):
        permissions = Permission.objects.filter(
            content_type__app_label="reception", codename__in=[
                f"{action}_speechtrainingsample" for action in actions
            ],
        )
        self.staff.user_permissions.add(*permissions)

    def test_audio_is_private_and_an_authorized_viewer_receives_nocache_wav(self):
        self.assertEqual(self.client.get(self.audio).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.audio).status_code, 403)
        self.grant("view")
        result = self.client.get(self.audio)
        self.assertEqual(result.status_code, 200)
        self.assertIn("no-store", result["Cache-Control"])
        self.assertEqual(result["Content-Type"], "audio/wav")
        with wave.open(io.BytesIO(result.content), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(wav.getnframes()), PCM)
        self.assertContains(self.client.get(self.change), self.audio)
        self.assertEqual(self.client.post(self.change, {"corrected_transcript": "Tomorrow"}).status_code, 403)
        SpeechTrainingSample.objects.update(corrected_transcript="Tomorrow")
        self.assertFalse(self.client.post(self.changelist, self.export).streaming)
        SpeechTrainingSample.objects.update(created_at=timezone.now() - timedelta(days=181))
        self.assertEqual(self.client.get(self.audio).status_code, 404)

    def test_reviewer_edits_correction_and_exports_only_corrected_pairs(self):
        self.grant("view", "change")
        self.client.force_login(self.staff)
        invalid = self.client.post(self.change, {"corrected_transcript": "guest@example.test"})
        self.assertContains(invalid, "Use a single line of text without contact details.")
        change = self.client.post(
            self.change, {"corrected_transcript": "the day after tomorrow", "transcript": "attempt to overwrite"},
        )
        self.assertEqual(change.status_code, 302)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.transcript, "the day before lawn")
        self.assertEqual(self.sample.corrected_transcript, "the day after tomorrow")
        self.assertTrue(record(2))
        uncorrected = SpeechTrainingSample.objects.get(turn_key=f"{2:064x}")

        pending = self.client.get(self.changelist, {"review": "pending"})
        self.assertEqual([sample.pk for sample in pending.context["cl"].result_list], [uncorrected.pk])
        result = self.client.post(self.changelist, {**self.export, "_selected_action": [self.sample.pk, uncorrected.pk]})
        self.assertEqual(result["Content-Type"], "application/zip")
        self.assertIn("no-store", result["Cache-Control"])
        with ZipFile(io.BytesIO(b"".join(result.streaming_content))) as archive:
            self.assertEqual(set(archive.namelist()), {"sample-0001.wav", "sample-0001.txt", "manifest.jsonl"})
            self.assertEqual(archive.read("sample-0001.txt"), b"the day after tomorrow\n")
            manifest = json.loads(archive.read("manifest.jsonl"))
            self.assertEqual(manifest, {
                "audio": "sample-0001.wav", "transcript": "sample-0001.txt", "language": "en", "stage": "check_in",
            })
            with wave.open(io.BytesIO(archive.read("sample-0001.wav")), "rb") as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), PCM)
