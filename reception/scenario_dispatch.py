"""Resolve approved booking outcomes against a browser conversation's pinned graph."""

import logging

from .conversation import _terminal, advance
from .models import ScenarioTransition
from .scenario_pinning import pinned_scenario_version_id, published_pinned_steps
from .scenario_step_settings import RETRY_EXHAUSTED_EVENT, RETRY_LIMIT_STEPS, retry_limit_value
from .scenario_validation import approved_destination


logger = logging.getLogger(__name__)
RETRY_COUNT = "scenario_retry_count"
RETRY_HANDOVER = "scenario_retry_handover"


def resolve_web_transition(state, event):
    """Return the approved destination, or None if the pinned edge is unavailable."""
    source = state["stage"]
    target = approved_destination(source, event)
    version_id = pinned_scenario_version_id(state)
    if not target or version_id is None:
        return target

    destination = (
        ScenarioTransition.objects.filter(
            source__in=published_pinned_steps(state).filter(key=source),
            event=event,
            target__version_id=version_id,
            target__key=target,
        )
        .values_list("target__key", flat=True)
        .first()
    )
    if destination is None:
        logger.warning("Pinned scenario version %s has no approved %s transition for %s.", version_id, event, source)
    return destination


def advance_web_turn(state, text, parsed, **options):
    """Count approved same-step retries on the pinned browser draft."""
    stage = state["stage"]
    if stage not in RETRY_LIMIT_STEPS or pinned_scenario_version_id(state) is None:
        return advance(state, text, parsed, resolve_transition=resolve_web_transition, **options)

    retried = False

    def resolve(current, event):
        nonlocal retried
        destination = resolve_web_transition(current, event)
        if event == "retry" and current["stage"] == stage and destination == stage:
            retried = True
        return destination

    result = advance(state, text, parsed, resolve_transition=resolve, **options)
    if retried and result["stage"] == stage:
        configuration = published_pinned_steps(state).filter(key=stage).values_list("configuration", flat=True).first()
        if limit := retry_limit_value(configuration):
            previous_count = state.get(RETRY_COUNT, 0)
            if type(previous_count) is not int or not 0 <= previous_count <= limit:
                previous_count = 0
            count = min(previous_count + 1, limit)
            if count == limit and resolve_web_transition(state, RETRY_EXHAUSTED_EVENT) == "handover":
                return {**_terminal(result, "handover"), RETRY_HANDOVER: True}
            return {**result, RETRY_COUNT: count}
    if RETRY_COUNT in result:
        result = result.copy()
        del result[RETRY_COUNT]
    return result
