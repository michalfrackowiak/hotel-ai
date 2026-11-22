"""Resolve approved booking outcomes against a browser conversation's pinned graph."""

import logging

from .models import ScenarioTransition
from .scenario_pinning import pinned_scenario_version_id, published_pinned_steps
from .scenario_validation import APPROVED_TRANSITIONS


logger = logging.getLogger(__name__)


def resolve_web_transition(state, event):
    """Return the approved destination, or None if the pinned edge is unavailable."""
    source = state["stage"]
    target = APPROVED_TRANSITIONS.get(source, {}).get(event)
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
