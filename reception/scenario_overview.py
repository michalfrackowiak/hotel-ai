"""Read-only projection of a saved scenario version for staff."""

from collections import defaultdict

from django.core.exceptions import ValidationError

from .scenario_materialization import plan_scenario_defaults
from .scenario_validation import (
    APPROVED_STEPS, TERMINAL_STEPS, scenario_entry_step, stored_scenario_transitions, validate_scenario_rows,
)


def _saved_graph(version):
    steps = list(version.steps.using(version._state.db).all()[:len(APPROVED_STEPS) + 1])
    transitions = list(stored_scenario_transitions(version, [step.pk for step in steps]))
    return [(step, step.configuration) for step in steps], transitions


def _effective_graph(version, *, language, channel):
    if version.published_at is not None or version.base_version_id is None:
        return _saved_graph(version)
    steps, edges, _ = plan_scenario_defaults(version, language=language, channel=channel)
    transitions = [(steps[source][0].pk, event, steps[target][0].pk) for (source, event), target in edges.items()]
    return list(steps.values()), transitions


def scenario_flow_overview(version):
    language, channel = version.scenario.language, version.scenario.channel
    require_outcomes = channel != "shared"
    errors = []
    fallback = False
    try:
        steps, transitions = _effective_graph(version, language=language, channel=channel)
    except ValidationError as error:
        errors = error.messages
        fallback = True
        steps, transitions = _saved_graph(version)
    else:
        try:
            validate_scenario_rows(
                language, channel, [(step.pk, step.key, configuration) for step, configuration in steps],
                transitions, require_outcomes=require_outcomes,
            )
        except ValidationError as error:
            errors = error.messages

    step_keys = {step.pk: step.key for step, _ in steps}
    entry = scenario_entry_step(language, step_keys.values(), require_outcomes=require_outcomes)
    outgoing = defaultdict(list)
    for source, event, target in transitions:
        outgoing[source].append((event, step_keys.get(target, "Outside this version")))
    return {
        "flow_entry": entry,
        "flow_errors": errors,
        "flow_saved_only": fallback,
        "flow_steps": [
            {
                "key": step.key, "position": step.position, "terminal": step.key in TERMINAL_STEPS,
                "inherited": step.version_id != version.pk, "outcomes": sorted(outgoing[step.pk]),
            }
            for step, _ in sorted(steps, key=lambda row: (row[0].position, row[0].key))
        ],
    }
