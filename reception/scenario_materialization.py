"""Copy a pinned shared graph into a channel draft before sealing it."""

from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.db.models import Q

from .models import ScenarioStep, ScenarioTransition, ScenarioVersion
from .scenario_step_settings import prompt_fields, validate_step_configuration
from .scenario_validation import APPROVED_STEPS, APPROVED_TRANSITIONS, MAX_TRANSITIONS


def _read_graph(version_id, language, channel, using):
    steps = {}
    keys_by_id = {}
    rows = (
        ScenarioStep.objects.using(using)
        .filter(version_id=version_id)
        .order_by("key")[:len(APPROVED_STEPS) + 1]
    )
    for step in rows:
        if step.key not in APPROVED_STEPS or step.key in steps or (step.key == "welcome" and language != "pl"):
            raise ValidationError("Shared defaults require a unique approved key for each step.")
        validate_step_configuration(language, channel, step.key, step.configuration)
        steps[step.key] = step
        keys_by_id[step.pk] = step.key

    transitions = {}
    rows = (
        ScenarioTransition.objects.using(using)
        .filter(Q(source_id__in=keys_by_id) | Q(target_id__in=keys_by_id))
        .order_by("source_id", "event")
        .values_list("source_id", "event", "target_id")[:MAX_TRANSITIONS + 1]
    )
    for source_id, event, target_id in rows:
        if source_id not in keys_by_id or target_id not in keys_by_id:
            raise ValidationError("Shared defaults cannot contain transitions between versions.")
        source, target = keys_by_id[source_id], keys_by_id[target_id]
        if (source, event) in transitions or APPROVED_TRANSITIONS.get(source, {}).get(event) != target:
            raise ValidationError("Shared defaults require unique approved booking transitions.")
        transitions[source, event] = target
    return steps, transitions


def materialize_scenario_defaults(draft):
    """Merge a persisted channel draft with its pinned template, keeping the reference.

    The publisher owns the outer transaction and serializes writes to the
    version and its graph. Any copies are rolled back if publication fails.
    An independent draft requires no copies.
    """
    using = draft._state.db
    if draft._state.adding or not using:
        raise ValidationError("Save the scenario version before materializing shared defaults.")
    if not connections[using].in_atomic_block:
        raise ValidationError("Materialize shared defaults inside the publication transaction.")

    with transaction.atomic(using=using):
        current = (
            ScenarioVersion.objects.using(using)
            .filter(pk=draft.pk)
            .values("scenario_id", "scenario__language", "scenario__channel", "base_version_id", "published_at")
            .first()
        )
        if (
            not current or current["published_at"] is not None or draft.published_at is not None
            or current["scenario_id"] != draft.scenario_id or current["base_version_id"] != draft.base_version_id
        ):
            raise ValidationError("Shared defaults can only be materialized on the current saved draft.")
        if current["base_version_id"] is None:
            return

        language = current["scenario__language"]
        channel = current["scenario__channel"]
        base_exists = ScenarioVersion.objects.using(using).filter(
            pk=current["base_version_id"],
            published_at__isnull=False,
            base_version_id__isnull=True,
            scenario__language=language,
            scenario__channel="shared",
        ).exists()
        if language not in ("en", "pl") or channel not in ("chat", "voice") or not base_exists:
            raise ValidationError("Shared defaults require a sealed, independent template in the same language.")

        base_steps, base_transitions = _read_graph(current["base_version_id"], language, "shared", using)
        channel_steps, channel_transitions = _read_graph(draft.pk, language, channel, using)
        for key, base_step in base_steps.items():
            inherited = {
                field: base_step.configuration[field]
                for field in prompt_fields(channel, key)
                if field in base_step.configuration
            }
            if local := channel_steps.get(key):
                configuration = {**inherited, **local.configuration}
                validate_step_configuration(language, channel, key, configuration)
                if configuration != local.configuration:
                    local.configuration = configuration
                    local.save(using=using, update_fields=["configuration"])
            else:
                validate_step_configuration(language, channel, key, inherited)
                channel_steps[key] = ScenarioStep.objects.using(using).create(
                    version_id=draft.pk, key=key, position=base_step.position, configuration=inherited,
                )

        for (source, event), target in base_transitions.items():
            if (source, event) not in channel_transitions:
                ScenarioTransition.objects.using(using).create(
                    source_id=channel_steps[source].pk, event=event, target_id=channel_steps[target].pk,
                )
