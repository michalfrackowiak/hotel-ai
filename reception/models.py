from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models, router
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone


PARSER_TRAINING_STAGES = ("check_in", "check_out", "guests", "room", "email", "phone")
PARSER_CONTACT_STAGES = ("email", "phone")
UNREVIEWED_SAMPLE_AGE = timedelta(days=30)
CORRECTED_SAMPLE_AGE = timedelta(days=180)


class Conversation(models.Model):
    conversation_id = models.CharField(max_length=40, primary_key=True, db_column="session_id")
    state = models.JSONField()
    expires_at = models.DateTimeField(null=True, db_index=True)


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


class ParserConfiguration(models.Model):
    DATE_ORDER_CHOICES = (("dmy", "Day / month / year"), ("mdy", "Month / day / year"))
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")), unique=True)
    numeric_date_order = models.CharField(max_length=3, choices=DATE_ORDER_CHOICES)
    allow_iso_dates = models.BooleanField(default=True)
    allow_slash_dates = models.BooleanField(default=True)
    allow_dot_dates = models.BooleanField(default=True)
    allow_dash_dates = models.BooleanField(default=True)
    allow_yearless_dates = models.BooleanField(default=True)
    allow_two_digit_years = models.BooleanField(default=True)

    class Meta:
        verbose_name = "parser configuration"
        verbose_name_plural = "parser configuration"

    def __str__(self):
        return f"{self.get_language_display()} parser settings"


class Scenario(models.Model):
    LANGUAGE_CHOICES = (("pl", "Polish"), ("en", "English"))
    CHANNEL_CHOICES = (("chat", "Chat"), ("voice", "Voice"), ("shared", "Shared defaults"))

    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    channel = models.CharField(max_length=6, choices=CHANNEL_CHOICES)
    published_version = models.ForeignKey(
        "ScenarioVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False,
    )

    class Meta:
        permissions = (("publish_scenario", "Can publish and roll back booking scenarios"),)
        constraints = [
            models.UniqueConstraint(fields=("language", "channel"), name="unique_scenario_scope"),
            models.CheckConstraint(condition=models.Q(language__in=("pl", "en")), name="valid_scenario_language"),
            models.CheckConstraint(condition=models.Q(channel__in=("chat", "voice", "shared")), name="valid_scenario_channel"),
        ]

    def __str__(self):
        return f"{self.get_language_display()} · {self.get_channel_display()}"

    def clean(self):
        if self.published_version_id and not ScenarioVersion.objects.db_manager(self._state.db).filter(
            pk=self.published_version_id, scenario_id=self.pk, published_at__isnull=False,
        ).exists():
            raise ValidationError({"published_version": "Select a sealed version of this scenario."})

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        using = using or router.db_for_write(type(self), instance=self)
        previous = (
            Scenario.objects.using(using)
            .filter(pk=self.pk)
            .values_list("language", "channel", "published_version_id")
            .first()
        )
        if previous and previous[:2] != (self.language, self.channel):
            raise ValidationError("The language and channel of an existing scenario cannot change.")
        if self.published_version_id != (previous[2] if previous else None):
            raise ValidationError("Use the publication workflow to change the selected version.")
        return super().save(
            force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields,
        )


class ScenarioVersion(models.Model):
    scenario = models.ForeignKey(Scenario, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    base_version = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived_versions")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("scenario", "number"), name="unique_scenario_version_number"),
            models.CheckConstraint(condition=models.Q(number__gte=1), name="positive_scenario_version_number"),
        ]

    def __str__(self):
        return f"{self.scenario} · v{self.number}"

    def _validate_base(self, using):
        if not self.base_version_id or not self.scenario_id:
            return
        scope = (
            Scenario.objects.using(using)
            .filter(pk=self.scenario_id)
            .values_list("language", "channel")
            .first()
        )
        if not scope or scope[1] == "shared" or not ScenarioVersion.objects.using(using).filter(
            pk=self.base_version_id, published_at__isnull=False, scenario__channel="shared", scenario__language=scope[0],
        ).exists():
            raise ValidationError({"base_version": "Select a sealed shared version in the same language."})

    def clean(self):
        self._validate_base(self._state.db or router.db_for_write(type(self), instance=self))

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        using = using or router.db_for_write(type(self), instance=self)
        previous = (
            ScenarioVersion.objects.using(using)
            .filter(pk=self.pk)
            .values_list("scenario_id", "number", "published_at")
            .first()
        )
        if self.published_at is not None or (previous and previous[2] is not None):
            raise ValidationError("Published scenario versions cannot be saved through the draft model.")
        if previous and previous[:2] != (self.scenario_id, self.number):
            raise ValidationError("The scenario and number of an existing version cannot change.")
        self._validate_base(using)
        return super().save(
            force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields,
        )

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        if ScenarioVersion.objects.using(using).filter(pk=self.pk, published_at__isnull=False).exists():
            raise ValidationError("Published scenario versions cannot be deleted through the draft model.")
        return super().delete(using=using, keep_parents=keep_parents)


class ScenarioStep(models.Model):
    version = models.ForeignKey(ScenarioVersion, on_delete=models.CASCADE, related_name="steps")
    key = models.SlugField(max_length=48)
    position = models.PositiveIntegerField(default=0)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("position", "key")
        constraints = [
            models.UniqueConstraint(fields=("version", "key"), name="unique_scenario_step_key"),
            models.CheckConstraint(condition=~models.Q(key=""), name="nonempty_scenario_step_key"),
        ]

    def __str__(self):
        return self.key

    def clean(self):
        if not isinstance(self.configuration, dict):
            raise ValidationError({"configuration": "Step configuration must be an object."})

    def _check_draft(self, using):
        previous_version_id = (
            ScenarioStep.objects.using(using).filter(pk=self.pk).values_list("version_id", flat=True).first()
        )
        if previous_version_id is not None and previous_version_id != self.version_id:
            raise ValidationError("An existing step cannot move to another scenario version.")
        if ScenarioVersion.objects.using(using).filter(pk=self.version_id, published_at__isnull=False).exists():
            raise ValidationError("Steps in a published scenario version cannot be changed.")

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        using = using or router.db_for_write(type(self), instance=self)
        self._check_draft(using)
        self.clean()
        return super().save(
            force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields,
        )

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        self._check_draft(using)
        return super().delete(using=using, keep_parents=keep_parents)


class ScenarioTransition(models.Model):
    source = models.ForeignKey(ScenarioStep, on_delete=models.CASCADE, related_name="outgoing_transitions")
    target = models.ForeignKey(ScenarioStep, on_delete=models.CASCADE, related_name="incoming_transitions")
    event = models.SlugField(max_length=48)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("source", "event"), name="unique_scenario_transition_event"),
            models.CheckConstraint(condition=~models.Q(event=""), name="nonempty_scenario_transition_event"),
        ]

    def __str__(self):
        return f"{self.source} → {self.target} ({self.event})"

    def _endpoint_version(self, using):
        endpoint_ids = {self.source_id, self.target_id}
        versions = dict(
            ScenarioStep.objects.using(using).filter(pk__in=endpoint_ids).values_list("pk", "version_id"),
        )
        if endpoint_ids != versions.keys() or len(set(versions.values())) != 1:
            raise ValidationError({"target": "Select existing steps in the same scenario version."})
        return versions[self.source_id]

    def clean(self):
        self._endpoint_version(self._state.db or router.db_for_write(type(self), instance=self))

    def _check_draft(self, using):
        previous_endpoints = (
            ScenarioTransition.objects.using(using).filter(pk=self.pk).values_list("source_id", "target_id").first()
        )
        if previous_endpoints and previous_endpoints != (self.source_id, self.target_id):
            raise ValidationError("Replace the transition to change its endpoints.")
        version_id = self._endpoint_version(using)
        if ScenarioVersion.objects.using(using).filter(pk=version_id, published_at__isnull=False).exists():
            raise ValidationError("Transitions in a published scenario version cannot be changed.")

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        using = using or router.db_for_write(type(self), instance=self)
        self._check_draft(using)
        return super().save(
            force_insert=force_insert, force_update=force_update, using=using, update_fields=update_fields,
        )

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        self._check_draft(using)
        return super().delete(using=using, keep_parents=keep_parents)


class ScenarioPublication(models.Model):
    """Retained selection history, independent of the actor account lifecycle."""

    scenario = models.ForeignKey(Scenario, on_delete=models.PROTECT, related_name="publication_history")
    previous_version = models.ForeignKey(
        ScenarioVersion, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
    )
    selected_version = models.ForeignKey(ScenarioVersion, on_delete=models.PROTECT, related_name="+")
    actor_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=8, choices=(("publish", "Publish"), ("rollback", "Rollback")))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pk",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=("publish", "rollback")), name="valid_scenario_publication_action",
            ),
            models.CheckConstraint(
                condition=~models.Q(previous_version=models.F("selected_version")),
                name="scenario_publication_changes_selection",
            ),
            models.CheckConstraint(condition=models.Q(actor_id__gte=1), name="positive_scenario_publication_actor"),
            models.UniqueConstraint(
                fields=("selected_version",), condition=models.Q(action="publish"), name="unique_scenario_version_seal_audit",
            ),
        ]


@receiver(pre_delete, sender=ScenarioVersion)
def _guard_scenario_version_delete(sender, instance, using, **kwargs):
    if ScenarioVersion.objects.using(using).filter(pk=instance.pk, published_at__isnull=False).exists():
        raise ValidationError("Published scenario versions cannot be deleted.")


@receiver(pre_delete, sender=ScenarioStep)
def _guard_scenario_step_delete(sender, instance, using, **kwargs):
    instance._check_draft(using)


@receiver(pre_delete, sender=ScenarioTransition)
def _guard_scenario_transition_delete(sender, instance, using, **kwargs):
    instance._check_draft(using)


@receiver(pre_delete, sender=ScenarioPublication)
def _guard_scenario_publication_delete(sender, instance, using, **kwargs):
    raise ValidationError("Scenario publication history cannot be deleted.")
