from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


SPEECH_TRAINING_STAGES = ("welcome", "check_in", "check_out", "guests", "room", "confirm", "delivery")
PARSER_TRAINING_STAGES = ("check_in", "check_out", "guests", "room", "email", "phone")
PARSER_CONTACT_STAGES = ("email", "phone")
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


class ScenarioPrompt(models.Model):
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")))
    key = models.CharField(max_length=32)
    chat_text = models.TextField(max_length=2000, blank=True)
    voice_parts = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "booking scenario prompt"
        verbose_name_plural = "booking scenario prompts"
        constraints = [models.UniqueConstraint(fields=("language", "key"), name="unique_scenario_prompt")]

    def __str__(self):
        return f"{self.get_language_display()} · {self.key.replace('_', ' ').capitalize()}"

    def clean(self):
        from .scenario_editor import validate_prompt

        errors = validate_prompt(self.language, self.key, self.chat_text, self.voice_parts)
        if errors:
            raise ValidationError(errors)


class ParserSamples(models.QuerySet):
    def unexpired(self, now=None):
        now = now or timezone.now()
        return self.filter(
            models.Q(created_at__gt=now - UNREVIEWED_SAMPLE_AGE)
            | (
                ~models.Q(stage__in=PARSER_CONTACT_STAGES)
                & ~models.Q(corrected_result="")
                & models.Q(created_at__gt=now - CORRECTED_SAMPLE_AGE)
            )
        )


class ParserSample(models.Model):
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")))
    stage = models.CharField(
        max_length=10, choices=tuple((stage, stage.replace("_", " ").title()) for stage in PARSER_TRAINING_STAGES),
    )
    source = models.CharField(max_length=5, choices=(("chat", "Chat"), ("voice", "Voice")))
    text = models.CharField(max_length=500, blank=True)
    parsed_result = models.CharField(max_length=500, blank=True)
    succeeded = models.BooleanField()
    corrected_result = models.CharField(max_length=500, blank=True, default="")
    rule_key = models.CharField(max_length=64, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    objects = ParserSamples.as_manager()

    class Meta:
        verbose_name = "parser training sample"
        verbose_name_plural = "parser training samples"

    def __str__(self):
        return f"Parser sample {self.pk}"
