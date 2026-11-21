"""Review and atomically select immutable booking scenario versions."""

from dataclasses import dataclass
from hashlib import sha256
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Scenario, ScenarioPublication, ScenarioStep, ScenarioTransition, ScenarioVersion
from .scenario_materialization import materialize_scenario_defaults
from .scenario_validation import APPROVED_STEPS, MAX_TRANSITIONS, validate_materialized_scenario


class ScenarioSelectionConflict(ValidationError):
    """The selected version or reviewed graph changed before confirmation."""


@dataclass(frozen=True)
class ScenarioSelectionReview:
    expected_version_id: int | None
    expected_audit_id: int | None
    fingerprint: str
    current_number: int | None
    candidate_number: int
    base_number: int | None
    diff: tuple[str, ...]


def _check_connection(using):
    connection = connections[using]
    if connection.vendor != "sqlite" or connection.settings_dict["OPTIONS"].get("transaction_mode") != "IMMEDIATE":
        raise ValidationError("Scenario publication requires the SQLite IMMEDIATE write transaction.")


def _authorized_staff(actor, using):
    actor_id = getattr(actor, "pk", None)
    if actor_id is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Scenario publication requires an active staff actor.")
    staff = get_user_model().objects.using(using).filter(pk=actor_id, is_active=True, is_staff=True).first()
    if staff is None or not staff.has_perm("reception.publish_scenario"):
        raise PermissionDenied("The staff actor does not have permission to publish scenarios.")
    for model in ("scenario", "scenarioversion", "scenariostep", "scenariotransition"):
        if not (staff.has_perm(f"reception.view_{model}") or staff.has_perm(f"reception.change_{model}")):
            raise PermissionDenied("Publication requires permission to review the complete scenario.")
    return staff


def _selection(scenario_id, version_id, using):
    scenario = Scenario.objects.using(using).filter(pk=scenario_id).first()
    if scenario is None:
        raise ValidationError("The scenario no longer exists.")
    version = ScenarioVersion.objects.using(using).filter(pk=version_id, scenario_id=scenario.pk).first()
    if version is None:
        raise ValidationError("Select a version belonging to this scenario.")
    audit_id = (
        ScenarioPublication.objects.using(using)
        .filter(scenario_id=scenario.pk)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first()
    )
    return scenario, version, audit_id


def _graph_snapshot(version_id, using):
    if version_id is None:
        return (), ()
    rows = list(
        ScenarioStep.objects.using(using)
        .filter(version_id=version_id)
        .order_by("key")
        .values_list("pk", "key", "position", "configuration")[:len(APPROVED_STEPS) + 1]
    )
    if len(rows) > len(APPROVED_STEPS):
        raise ValidationError("This graph has too many steps to review.")
    keys = {pk: key for pk, key, _, _ in rows}
    transitions = tuple(
        ScenarioTransition.objects.using(using)
        .filter(Q(source_id__in=keys) | Q(target_id__in=keys))
        .order_by("source__key", "event", "target__key")
        .values_list("source__key", "event", "target__key")[:MAX_TRANSITIONS + 1]
    )
    if len(transitions) > MAX_TRANSITIONS:
        raise ValidationError("This graph has too many transitions to review.")
    return tuple((key, position, config) for _, key, position, config in rows), transitions


def _describe_step(position, configuration):
    parts = [f"position {position}"]
    if not isinstance(configuration, dict):
        return f"position {position}, invalid saved prompt configuration"
    if text := configuration.get("chat_text"):
        parts.append(f"chat: {text}")
    voice = configuration.get("voice_parts")
    if isinstance(voice, list):
        for number, segment in enumerate(voice, 1):
            if isinstance(segment, dict):
                listen = ", listen" if segment.get("listen") else ""
                parts.append(
                    f"voice {number}: {segment.get('text', '')} ({segment.get('seconds', 0)}s{listen})",
                )
    return " | ".join(parts)


def _graph_diff(current, candidate):
    current_steps, current_transitions = current
    candidate_steps, candidate_transitions = candidate
    previous = {key: (position, configuration) for key, position, configuration in current_steps}
    proposed = {key: (position, configuration) for key, position, configuration in candidate_steps}
    for key in sorted(previous.keys() | proposed.keys()):
        if key not in previous:
            yield f"Add step {key} ({_describe_step(*proposed[key])})"
        elif key not in proposed:
            yield f"Remove step {key} ({_describe_step(*previous[key])})"
        elif previous[key] != proposed[key]:
            yield f"Change step {key} from {_describe_step(*previous[key])} to {_describe_step(*proposed[key])}"
    old_edges, new_edges = set(current_transitions), set(candidate_transitions)
    for source, event, target in sorted(old_edges - new_edges):
        yield f"Remove transition {source} → {target} ({event})"
    for source, event, target in sorted(new_edges - old_edges):
        yield f"Add transition {source} → {target} ({event})"


def _prepare_selection(scenario, version, audit_id, actor_id, action, using):
    if action not in ("publish", "rollback"):
        raise ValidationError("Select a supported scenario publication operation.")
    if version.pk == scenario.published_version_id:
        raise ValidationError("The requested version is already selected.")
    current = _graph_snapshot(scenario.published_version_id, using)
    candidate = _graph_snapshot(version.pk, using)
    base = _graph_snapshot(version.base_version_id, using)
    if action == "publish":
        if version.published_at is not None:
            raise ValidationError("Select a draft version to publish.")
        materialize_scenario_defaults(version)
    elif version.published_at is None or not ScenarioPublication.objects.using(using).filter(
        scenario_id=scenario.pk, selected_version_id=version.pk, action="publish",
    ).exists():
        raise ValidationError("Select a previously published version to roll back to.")
    validate_materialized_scenario(version)
    effective = _graph_snapshot(version.pk, using) if version.base_version_id and action == "publish" else candidate
    current_number = scenario.published_version.number if scenario.published_version_id else None
    base_number = version.base_version.number if version.base_version_id else None
    payload = (
        scenario.pk, scenario.language, scenario.channel, scenario.published_version_id, audit_id, actor_id,
        action, current_number, version.pk, version.number, version.base_version_id, base_number,
        current, candidate, base, effective,
    )
    fingerprint = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return ScenarioSelectionReview(
        expected_version_id=scenario.published_version_id, expected_audit_id=audit_id, fingerprint=fingerprint,
        current_number=current_number, candidate_number=version.number, base_number=base_number,
        diff=tuple(_graph_diff(current, effective)),
    )


def review_scenario_selection(scenario_id, version_id, *, actor, action, using=DEFAULT_DB_ALIAS):
    """Review the exact graph under a rollback-only materialization savepoint."""
    _check_connection(using)
    with transaction.atomic(using=using):
        staff = _authorized_staff(actor, using)
        scenario, version, audit_id = _selection(scenario_id, version_id, using)
        with transaction.atomic(using=using):
            review = _prepare_selection(scenario, version, audit_id, staff.pk, action, using)
            transaction.set_rollback(True, using=using)
        return review


def publish_scenario_version(
    scenario_id, version_id, *, expected_version_id, expected_audit_id, expected_fingerprint, actor,
    using=DEFAULT_DB_ALIAS,
):
    """Materialize and seal a reviewed draft, select it, and record one audit event."""
    return _select_version(
        scenario_id, version_id, expected_version_id, expected_audit_id, expected_fingerprint, actor, using,
        action="publish",
    )


def rollback_scenario_version(
    scenario_id, version_id, *, expected_version_id, expected_audit_id, expected_fingerprint, actor,
    using=DEFAULT_DB_ALIAS,
):
    """Select a reviewed, prior sealed version from the same scope."""
    return _select_version(
        scenario_id, version_id, expected_version_id, expected_audit_id, expected_fingerprint, actor, using,
        action="rollback",
    )


def _select_version(
    scenario_id, version_id, expected_version_id, expected_audit_id, expected_fingerprint, actor, using, *, action,
):
    _check_connection(using)
    with transaction.atomic(using=using):
        staff = _authorized_staff(actor, using)
        try:
            scenario, version, audit_id = _selection(scenario_id, version_id, using)
        except ValidationError as error:
            raise ScenarioSelectionConflict("The reviewed scenario or version is no longer available.") from error
        if scenario.published_version_id != expected_version_id or audit_id != expected_audit_id:
            raise ScenarioSelectionConflict("The selection or publication history changed. Review the scenario again.")
        try:
            review = _prepare_selection(scenario, version, audit_id, staff.pk, action, using)
        except ValidationError as error:
            raise ScenarioSelectionConflict("The reviewed graph is no longer valid. Review the scenario again.") from error
        if review.fingerprint != expected_fingerprint:
            raise ScenarioSelectionConflict("The reviewed graph changed. Review the scenario again.")

        if action == "publish":
            sealed = ScenarioVersion.objects.using(using).filter(
                pk=version.pk, scenario_id=scenario.pk, published_at__isnull=True,
            ).update(published_at=timezone.now())
            if sealed != 1:
                raise ScenarioSelectionConflict("The draft changed before it could be sealed.")
        selected = Scenario.objects.using(using).filter(
            pk=scenario.pk, published_version_id=expected_version_id,
        ).update(published_version_id=version.pk)
        if selected != 1:
            raise ScenarioSelectionConflict("The selected version changed before the operation completed.")

        return ScenarioPublication.objects.using(using).create(
            scenario_id=scenario.pk,
            previous_version_id=expected_version_id,
            selected_version_id=version.pk,
            actor_id=staff.pk,
            action=action,
        )
