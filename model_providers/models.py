from urllib.parse import urlsplit
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models


class AgentConfiguration(models.Model):
    class Provider(models.TextChoices):
        DEMO = "demo", "Built-in demo"
        HOSTED = "hosted", "Hosted OpenAI-compatible API"
        OLLAMA = "ollama", "Self-hosted Ollama"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider = models.CharField(max_length=12, choices=Provider.choices, default=Provider.DEMO)
    model = models.CharField(max_length=120, blank=True, help_text="Model name from the selected provider.")

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="single_agent_configuration")]
        verbose_name = "reception agent configuration"
        verbose_name_plural = "reception agent configuration"

    def clean(self):
        if self.provider == self.Provider.DEMO:
            return
        if not self.model.strip():
            raise ValidationError({"model": "Enter the model name for this provider."})
        if self.provider == self.Provider.HOSTED and not valid_endpoint(settings.HOTEL_MODEL_BASE_URL, schemes={"https"}):
            raise ValidationError("Set HOTEL_MODEL_BASE_URL to an HTTPS API base URL without credentials or query parameters.")

    def __str__(self):
        return self.get_provider_display()


def valid_endpoint(value, *, schemes):
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return False
    return bool(
        url.scheme in schemes and url.hostname and url.username is None and url.password is None
        and not url.query and not url.fragment and (port is None or port > 0)
    )


SPEECH_LANGUAGES = (("en", "English"), ("pl", "Polish"))
PINNED_IMAGE = RegexValidator(
    r"\A(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]{1,5})?/)?(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*[a-z0-9]+(?:[._-][a-z0-9]+)*@sha256:[0-9a-f]{64}\Z",
    "Enter a repository pinned with @sha256: followed by 64 lowercase hexadecimal characters; no tag, credentials or command.",
)
MODEL_CHECKSUM = RegexValidator(r"\A[0-9a-f]{64}\Z", "Enter a SHA-256 checksum using 64 lowercase hexadecimal characters.")


def validate_speech_languages(value):
    if not isinstance(value, list) or not value or any(item not in ("en", "pl") for item in value) or len(set(value)) != len(value):
        raise ValidationError("Select English, Polish, or both without duplicates.")


class SpeechProvider(models.Model):
    class Task(models.TextChoices):
        STT = "stt", "Speech to text"
        TTS = "tts", "Text to speech"

    class Architecture(models.TextChoices):
        ARM64 = "arm64", "ARM64"
        AMD64 = "amd64", "AMD64 / x86-64"
        MULTI = "multi", "ARM64 and AMD64"

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    task = models.CharField(max_length=3, choices=Task.choices)
    engine = models.CharField(max_length=80)
    languages = models.JSONField(default=list, validators=[validate_speech_languages])
    architecture = models.CharField(max_length=5, choices=Architecture.choices)
    image = models.CharField(max_length=255, blank=True, validators=[PINNED_IMAGE], help_text="Optional for a draft. Catalog metadata only: saving does not pull or run this image.")
    cpu_limit = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("1.00"), validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("8.00"))])
    memory_mib = models.PositiveIntegerField(default=1024, validators=[MinValueValidator(64), MaxValueValidator(16384)])
    license = models.CharField(max_length=200, blank=True, help_text="Engine license metadata; verify model/voice licenses separately.")
    notes = models.TextField(blank=True, max_length=4000)
    adapter_version = models.PositiveSmallIntegerField(default=1, editable=False)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.CheckConstraint(condition=models.Q(cpu_limit__gte=Decimal("0.01"), cpu_limit__lte=8), name="speech_cpu_budget"),
            models.CheckConstraint(condition=models.Q(memory_mib__gte=64, memory_mib__lte=16384), name="speech_memory_budget"),
            models.CheckConstraint(condition=models.Q(adapter_version=1), name="speech_adapter_v1"),
        ]

    def __str__(self):
        return self.name


class SpeechModel(models.Model):
    provider = models.ForeignKey(SpeechProvider, on_delete=models.CASCADE, related_name="speech_models")
    language = models.CharField(max_length=2, choices=SPEECH_LANGUAGES)
    name = models.CharField(max_length=160)
    checksum = models.CharField(max_length=64, blank=True, validators=[MODEL_CHECKSUM], help_text="Optional draft SHA-256 of the model or voice artifact.")
    license = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "speech model or voice"
        verbose_name_plural = "speech models and voices"
        constraints = [models.UniqueConstraint(fields=("provider", "language", "name"), name="speech_model_identity")]

    def clean(self):
        provider = self._state.fields_cache.get("provider")
        if provider is None and self.provider_id:
            provider = self.provider
        if provider is not None and self.language not in provider.languages:
            raise ValidationError({"language": "This language is not supported by the provider."})

    def __str__(self):
        return self.name
