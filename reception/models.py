from datetime import timedelta

from django.db import models
from django.utils import timezone


SPEECH_TRAINING_STAGES = ("welcome", "check_in", "check_out", "guests", "room", "confirm", "delivery")
UNREVIEWED_SAMPLE_AGE = timedelta(days=30)
CORRECTED_SAMPLE_AGE = timedelta(days=180)


class Conversation(models.Model):
    session = models.OneToOneField("sessions.Session", on_delete=models.CASCADE, primary_key=True)
    state = models.JSONField()


class StaffHandover(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")))
    created_at = models.DateTimeField(auto_now_add=True)
    closed = models.BooleanField(default=False)


class HandoverMessage(models.Model):
    handover = models.ForeignKey(StaffHandover, on_delete=models.CASCADE, related_name="messages")
    staff = models.BooleanField(default=False)
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class SpeechTrainingSamples(models.QuerySet):
    def unexpired(self, now=None):
        now = now or timezone.now()
        return self.filter(
            models.Q(corrected_transcript="", created_at__gt=now - UNREVIEWED_SAMPLE_AGE)
            | (~models.Q(corrected_transcript="") & models.Q(created_at__gt=now - CORRECTED_SAMPLE_AGE))
        )


class SpeechTrainingSample(models.Model):
    turn_key = models.CharField(max_length=64, unique=True, editable=False)
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")))
    stage = models.CharField(
        max_length=10, choices=tuple((stage, stage.replace("_", " ").title()) for stage in SPEECH_TRAINING_STAGES),
    )
    pcm = models.BinaryField()
    transcript = models.CharField(max_length=500, blank=True)
    corrected_transcript = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = SpeechTrainingSamples.as_manager()

    def __str__(self):
        return f"Speech sample {self.pk}"
