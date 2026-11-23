"""In-memory preview of one browser arrival reply using the built-in date parser."""

from datetime import date

from django import forms

from .conversation import new_conversation, selects_english
from .parsing import StayDates, parse_stay_input
from .scenario import render_chat, split_voice_parts, voice_segments
from .scenario_dispatch import advance_counted_turn
from .scenario_pinned_prompts import browser_prompt_bindings, browser_prompt_overrides
from .scenario_step_settings import ARRIVAL_INPUT_FIELD, REQUIRE_ARRIVAL_RANGE, retry_limit_value
from .scenario_validation import approved_destination
from .speech import MAX_TEXT_CHARACTERS


class ArrivalSimulationForm(forms.Form):
    reply = forms.CharField(label="Example guest reply", max_length=500)


def simulate_arrival(language, channel, steps, transitions, reply):
    if language == "pl" and selects_english(reply):
        return {"unsupported": True}

    step_keys = {step.pk: step.key for step, _ in steps}
    edges = {(step_keys[source], event): step_keys[target] for source, event, target in transitions}
    configurations = {step.key: configuration for step, configuration in steps}
    configuration = configurations["check_in"]
    decisions = []

    def resolve(state, event):
        source = state["stage"]
        target = edges.get((source, event))
        allowed = target is not None and target == approved_destination(source, event)
        decisions.append((source, event, target if allowed else None))
        return target if allowed else None

    parsed = parse_stay_input("check_in", reply, language)
    require_range = parsed is not None and configuration.get(ARRIVAL_INPUT_FIELD) == REQUIRE_ARRIVAL_RANGE
    result = advance_counted_turn(
        new_conversation(language, "check_in"), reply, parsed, resolve, lambda: retry_limit_value(configuration),
        voice=channel == "voice", require_arrival_range=require_range,
    )
    bindings = browser_prompt_bindings(result, channel)
    overrides = browser_prompt_overrides(language, channel, bindings, (
        (key, configurations[key]) for key in bindings if key in configurations
    ))
    voice = []
    for part in voice_segments(result, overrides=overrides):
        voice.extend(split_voice_parts([part]) if len(part["text"]) > MAX_TEXT_CHARACTERS else [part])

    arrival = parsed.check_in if isinstance(parsed, StayDates) else parsed if type(parsed) is date else None
    return {
        "arrival": arrival.isoformat() if arrival else None,
        "departure": parsed.check_out.isoformat() if isinstance(parsed, StayDates) else None,
        "decisions": decisions,
        "stage": result["stage"],
        "chat": render_chat(result, overrides=overrides),
        "voice": voice,
        "voice_fallback": result["reply"],
    }
