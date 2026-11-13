from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
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
