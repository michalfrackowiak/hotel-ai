"""Validate standalone persisted graphs without publishing or executing them.

The outcome contract covers the new web booking flow in one language and
channel. Language selection across scopes, older drafts, and inbound phone
continue to belong to the existing engines. These checks do not implement
dispatch or attest that a candidate is safe to publish.
"""

from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import ScenarioStep, ScenarioTransition
from .scenario_step_settings import validate_step_configuration


TERMINAL_STEPS = frozenset({"completed", "cancelled", "handover"})
CONTACT_CHANGES = {
    "change_delivery": "delivery",
    "choose_email": "email",
    "choose_sms": "phone",
}
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
    "guests": {
        "available": "room",
        "retry": "guests",
        "unavailable": "check_in",
        "offer_changed": "check_in",
    },
    "room": {"selected": "confirm", "retry": "room"},
    "confirm": {"approve_quote": "delivery", "decline_quote": "cancelled", "retry": "confirm"},
    "delivery": {"email": "email", "sms": "phone", "retry": "delivery"},
    "email": {
        **CONTACT_CHANGES, "valid_contact": "contact_confirm", "retry": "email", "reception": "handover",
    },
    "phone": {
        **CONTACT_CHANGES, "valid_contact": "contact_confirm", "retry": "phone", "reception": "handover",
    },
    "contact_confirm": {
        **CONTACT_CHANGES,
        "approve_contact": "delivery_pending",
        "approve_contact_without_wait": "completed",
        "edit_email": "email",
        "edit_phone": "phone",
        "invalid_email": "email",
        "invalid_phone": "phone",
        "replace_contact": "contact_confirm",
        "retry": "contact_confirm",
        "reception": "handover",
        "offer_changed": "check_in",
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

# The existing actions return a new draft on an unavailable or changed offer.
# Contact recovery clears the prior candidate, or requires readback of its
# replacement. These named events may revisit a step; arbitrary back edges
# still fail validation, and the checks below include recovery in reachability.
RECOVERY_EVENTS = {
    "guests": frozenset({"unavailable", "offer_changed"}),
    "email": frozenset(CONTACT_CHANGES),
    "phone": frozenset(CONTACT_CHANGES),
    "contact_confirm": frozenset({
        *CONTACT_CHANGES, "edit_email", "edit_phone", "invalid_email", "invalid_phone", "replace_contact",
        "offer_changed",
    }),
}


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
    """Preview the structure of a saved graph, including incomplete drafts."""
    _validate_scenario(version, require_outcomes=False)


def validate_scenario_outcomes(version):
    """Require explicit routes for all supported, in-scope web booking outcomes."""
    _validate_scenario(version, require_outcomes=True)


def _validate_scenario(version, *, require_outcomes):
    # A future caller using this as a publication prerequisite has to protect
    # the entire graph snapshot against concurrent changes.
    if version._state.adding or not version._state.db:
        raise ValidationError("Save the scenario version before validating its graph.")
    stored = (
        type(version).objects.using(version._state.db)
        .filter(pk=version.pk)
        .values("base_version_id", "scenario__language", "scenario__channel")
        .first()
    )
    if stored is None:
        raise ValidationError("The scenario version no longer exists.")
    if version.base_version_id is not None or stored["base_version_id"] is not None:
        raise ValidationError("Materialize the shared defaults before validating this scenario version.")
    language, channel = stored["scenario__language"], stored["scenario__channel"]
    if require_outcomes and (language not in ("en", "pl") or channel not in ("chat", "voice")):
        raise ValidationError("Outcome validation requires an English or Polish web chat or browser voice scope.")

    steps = list(
        ScenarioStep.objects.using(version._state.db)
        .filter(version_id=version.pk)
        .order_by("pk")
        .values_list("pk", "key", "configuration")[:len(APPROVED_STEPS) + 1]
    )
    step_keys = {key for _, key, _ in steps}
    if len(step_keys) != len(steps) or not step_keys <= APPROVED_STEPS:
        raise ValidationError("Each step must have a unique approved booking step key.")
    for _, key, configuration in steps:
        try:
            validate_step_configuration(language, channel, key, configuration)
        except ValidationError as error:
            raise ValidationError([f"{key}: {message}" for message in error.messages]) from error
    if "welcome" in step_keys and language != "pl":
        raise ValidationError("Only Polish scenarios may use the bilingual welcome step.")
    entry = "welcome" if "welcome" in step_keys or (require_outcomes and language == "pl") else "check_in"
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

    # Retry and explicitly named recovery edges are not independent progress.
    dependencies = {
        key: [
            target for event, target in outgoing[key]
            if event != "retry" and event not in RECOVERY_EVENTS.get(key, ())
        ]
        for key in step_keys
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
    if require_outcomes:
        required = {
            (source, event)
            for source, outcomes in APPROVED_TRANSITIONS.items()
            if source != "welcome" or language == "pl"
            for event in outcomes
        }
        if missing_outcomes := required - edges:
            raise ValidationError(
                "Missing booking outcomes: %(outcomes)s.",
                params={"outcomes": ", ".join(f"{source}.{event}" for source, event in sorted(missing_outcomes))},
            )
