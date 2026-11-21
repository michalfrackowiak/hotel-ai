"""Validate a persisted, standalone scenario graph without sealing or running it.

Only approved booking events with known destinations are accepted. Inheritance,
step configuration, and transitions back to earlier steps require separate
validation contracts before they can be used in a publication candidate.
"""

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import ScenarioStep, ScenarioTransition


TERMINAL_STEPS = frozenset({"completed", "cancelled", "handover"})
APPROVED_TRANSITIONS = {
    "welcome": {"continue": "check_in", "timeout": "check_in", "arrival": "check_out", "stay_dates": "guests"},
    "check_in": {
        "arrival": "check_out",
        "stay_dates": "guests",
        "retry": "check_in",
        "unrecognized": "handover",
        "timeout": "handover",
    },
    "check_out": {"departure": "guests", "retry": "check_out"},
    "guests": {"available": "room", "retry": "guests"},
    "room": {"selected": "confirm", "retry": "room"},
    "confirm": {"approve_quote": "delivery", "decline_quote": "cancelled", "retry": "confirm"},
    "delivery": {"email": "email", "sms": "phone", "retry": "delivery"},
    "email": {"valid_contact": "contact_confirm", "retry": "email", "reception": "handover"},
    "phone": {"valid_contact": "contact_confirm", "retry": "phone", "reception": "handover"},
    "contact_confirm": {
        "approve_contact": "delivery_pending",
        "approve_contact_without_wait": "completed",
        "retry": "contact_confirm",
        "reception": "handover",
    },
    "delivery_pending": {
        "dispatch_accepted": "completed",
        "dispatch_pending": "completed",
        "dispatch_unverified": "completed",
        "retry": "delivery_pending",
    },
}
APPROVED_STEPS = TERMINAL_STEPS | frozenset(APPROVED_TRANSITIONS)
MAX_TRANSITIONS = sum(map(len, APPROVED_TRANSITIONS.values()))
QUOTE_APPROVAL = frozenset({("confirm", "approve_quote")})
CONTACT_APPROVAL = frozenset({
    ("contact_confirm", "approve_contact"), ("contact_confirm", "approve_contact_without_wait"),
})


def _reachable(entry, outgoing, *, excluded=frozenset()):
    visited = set()
    pending = [entry]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(target for event, target in outgoing[current] if (current, event) not in excluded)
    return visited


def validate_scenario_graph(version):
    """Raise ValidationError for an unsupported or unsafe persisted candidate.

    A future publisher must call this under the lock or transaction protecting
    the graph snapshot. A successful result does not authorize execution.
    """
    if version._state.adding or not version._state.db:
        raise ValidationError("Save the scenario version before validating its graph.")
    stored = (
        type(version).objects.using(version._state.db)
        .filter(pk=version.pk)
        .values("base_version_id", "scenario__language")
        .first()
    )
    if stored is None:
        raise ValidationError("The scenario version no longer exists.")
    if version.base_version_id is not None or stored["base_version_id"] is not None:
        raise ValidationError("Materialize the shared defaults before validating this scenario version.")

    steps = list(
        ScenarioStep.objects.using(version._state.db)
        .filter(version_id=version.pk)
        .order_by("pk")
        .values_list("pk", "key", "configuration")[:len(APPROVED_STEPS) + 1]
    )
    step_keys = {key for _, key, _ in steps}
    if len(step_keys) != len(steps) or not step_keys <= APPROVED_STEPS:
        raise ValidationError("Each step must have a unique approved booking step key.")
    if any(configuration != {} for _, _, configuration in steps):
        raise ValidationError("Step configuration is not yet supported by graph validation.")
    if "welcome" in step_keys and stored["scenario__language"] != "pl":
        raise ValidationError("Only Polish scenarios may use the bilingual welcome step.")
    entry = "welcome" if "welcome" in step_keys else "check_in"
    missing = {entry, "confirm", "contact_confirm", "completed"} - step_keys
    if missing:
        raise ValidationError("Missing required scenario steps: %(steps)s.", params={"steps": ", ".join(sorted(missing))})

    key_by_id = {pk: key for pk, key, _ in steps}
    transitions = ScenarioTransition.objects.using(version._state.db).filter(
        Q(source_id__in=key_by_id) | Q(target_id__in=key_by_id),
    ).order_by("pk").values_list("source_id", "event", "target_id")[:MAX_TRANSITIONS + 1]
    outgoing = defaultdict(list)
    edges = set()
    for source_id, event, target_id in transitions:
        if source_id not in key_by_id or target_id not in key_by_id:
            raise ValidationError("Each transition must connect steps in this scenario version.")
        source, target = key_by_id[source_id], key_by_id[target_id]
        if (source, event) in edges or APPROVED_TRANSITIONS.get(source, {}).get(event) != target:
            raise ValidationError(
                "Unapproved or duplicate transition: %(source)s → %(target)s (%(event)s).",
                params={"source": source, "target": target, "event": event},
            )
        edges.add((source, event))
        outgoing[source].append((event, target))

    # Exclude the explicitly approved self-retries from the progress graph.
    dependencies = {
        key: [target for event, target in outgoing[key] if event != "retry"] for key in step_keys
    }
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as error:
        raise ValidationError("Scenario transitions must make progress except during an approved retry.") from error

    unreachable = step_keys - _reachable(entry, outgoing)
    if unreachable:
        raise ValidationError("Unreachable scenario steps: %(steps)s.", params={"steps": ", ".join(sorted(unreachable))})
    dead_ends = {key for key in step_keys - TERMINAL_STEPS if not dependencies[key]}
    if dead_ends:
        raise ValidationError(
            "Nonterminal scenario steps need a progress transition: %(steps)s.",
            params={"steps": ", ".join(sorted(dead_ends))},
        )
    if any("completed" in _reachable(entry, outgoing, excluded=approval) for approval in (QUOTE_APPROVAL, CONTACT_APPROVAL)):
        raise ValidationError("Every path to completion must include quote and contact approval.")
