"""Copy a pinned shared graph into a channel draft before sealing it."""

from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.db.models import Q

from .models import ScenarioStep, ScenarioTransition, ScenarioVersion
from .scenario_step_settings import prompt_fields, validate_step_configuration
from .scenario_validation import APPROVED_STEPS, MAX_TRANSITIONS, approved_destination


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
        if (source, event) in transitions or approved_destination(source, event) != target:
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

        steps, transitions, inherited_edges = plan_scenario_defaults(
            draft, language=current["scenario__language"], channel=current["scenario__channel"],
        )
        persisted = {}
        for key, (step, configuration) in steps.items():
            if step.version_id != draft.pk:
                step = ScenarioStep.objects.using(using).create(
                    version_id=draft.pk, key=key, position=step.position, configuration=configuration,
                )
            elif configuration != step.configuration:
                step.configuration = configuration
                step.save(using=using, update_fields=["configuration"])
            persisted[key] = step
        for source, event in inherited_edges:
            target = transitions[source, event]
            ScenarioTransition.objects.using(using).create(
                source_id=persisted[source].pk, event=event, target_id=persisted[target].pk,
            )


def plan_scenario_defaults(draft, *, language, channel):
    """Return the effective steps and transitions of an inheriting draft without writing rows."""
    using = draft._state.db
    base_exists = ScenarioVersion.objects.using(using).filter(
        pk=draft.base_version_id,
        published_at__isnull=False,
        base_version_id__isnull=True,
        scenario__language=language,
        scenario__channel="shared",
    ).exists()
    if language not in ("en", "pl") or channel not in ("chat", "voice") or not base_exists:
        raise ValidationError("Shared defaults require a sealed, independent template in the same language.")

    base_steps, base_transitions = _read_graph(draft.base_version_id, language, "shared", using)
    local_steps, local_transitions = _read_graph(draft.pk, language, channel, using)
    steps = {key: (step, step.configuration) for key, step in local_steps.items()}
    for key, base_step in base_steps.items():
        inherited = {
            field: base_step.configuration[field]
            for field in prompt_fields(language, channel, key)
            if field in base_step.configuration
        }
        local = local_steps.get(key)
        configuration = {**inherited, **local.configuration} if local else inherited
        validate_step_configuration(language, channel, key, configuration)
        steps[key] = (local or base_step, configuration)

    inherited_edges = tuple(edge for edge in base_transitions if edge not in local_transitions)
    return steps, {**base_transitions, **local_transitions}, inherited_edges
