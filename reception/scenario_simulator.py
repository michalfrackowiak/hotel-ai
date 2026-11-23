"""In-memory preview of one browser booking reply using built-in parsing and fictional inventory."""

from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from bookings.services import RoomOffer

from .conversation import advance, new_conversation, selects_english
from .parsing import StayDates, parse_stay_input
from .scenario import render_chat, split_voice_parts, voice_segments
from .scenario_dispatch import advance_counted_turn
from .scenario_pinned_prompts import browser_prompt_bindings, browser_prompt_overrides
from .scenario_step_settings import ARRIVAL_INPUT_FIELD, REQUIRE_ARRIVAL_RANGE, retry_limit_value
from .scenario_validation import approved_destination
from .speech import MAX_TEXT_CHARACTERS


FICTIONAL_ROOMS = (
    (-1, "Demo Double", 2, Decimal("120")),
    (-2, "Demo Family", 6, Decimal("200")),
)


class BookingReplySimulationForm(forms.Form):
    stage = forms.ChoiceField(label="Booking step", choices=(
        ("check_in", "Arrival"), ("check_out", "Departure"), ("guests", "Guest count"),
        ("room", "Room selection"), ("confirm", "Quote decision"),
        ("delivery", "Delivery choice"), ("email", "Email entry"), ("phone", "Phone entry"),
    ))
    reply = forms.CharField(label="Example guest reply", max_length=500)


def _fictional_room_search(*, check_in, check_out, guests):
    nights = (check_out - check_in).days
    return [
        RoomOffer(room_id, name, capacity, rate, rate * nights)
        for room_id, name, capacity, rate in FICTIONAL_ROOMS
        if guests <= capacity
    ]


def simulate_booking_reply(language, channel, steps, transitions, *, stage, reply):
    if stage == "check_in" and language == "pl" and selects_english(reply):
        return {"unsupported": True}

    step_keys = {step.pk: step.key for step, _ in steps}
    edges = {(step_keys[source], event): step_keys[target] for source, event, target in transitions}
    configurations = {step.key: configuration for step, configuration in steps}
    configuration = configurations[stage]
    decisions = []

    def resolve(state, event):
        source = state["stage"]
        target = edges.get((source, event))
        allowed = target is not None and target == approved_destination(source, event)
        decisions.append((source, event, target if allowed else None))
        return target if allowed else None

    contact_entry = stage in {"email", "phone"}
    needs_delivery = stage == "delivery" or contact_entry
    needs_room_offer = stage in {"room", "confirm"} or needs_delivery
    state = new_conversation(language, "guests" if needs_room_offer else stage)
    state["stage"] = "guests" if needs_room_offer else stage
    check_in = check_out = None
    if stage != "check_in":
        check_in = timezone.localdate() + timedelta(days=1)
        state["check_in"] = check_in.isoformat()
    if stage == "guests" or needs_room_offer:
        check_out = check_in + timedelta(days=2)
        state["check_out"] = check_out.isoformat()
    if needs_room_offer:
        # Prepare a two-guest example independently of the edge for the turn being simulated.
        state = advance(state, "", 2, voice=channel == "voice", room_search=_fictional_room_search)
        if stage != "room":
            state = advance(state, "1", None)
        if needs_delivery:
            state = advance(state, "tak" if language == "pl" else "yes", None)
        if contact_entry:
            state = advance(state, "email" if stage == "email" else "sms", None)
    parsed = parse_stay_input(stage, reply, language, check_in=check_in)
    require_range = parsed is not None and configuration.get(ARRIVAL_INPUT_FIELD) == REQUIRE_ARRIVAL_RANGE
    result = advance_counted_turn(
        state, reply, parsed, resolve, lambda: retry_limit_value(configuration),
        voice=channel == "voice", require_arrival_range=require_range, room_search=_fictional_room_search,
    )
    bindings = browser_prompt_bindings(result, channel)
    overrides = browser_prompt_overrides(language, channel, bindings, (
        (key, configurations[key]) for key in bindings if key in configurations
    ))
    voice = []
    for part in voice_segments(result, overrides=overrides):
        voice.extend(split_voice_parts([part]) if len(part["text"]) > MAX_TEXT_CHARACTERS else [part])

    arrival = departure = None
    if isinstance(parsed, StayDates):
        arrival, departure = parsed.check_in, parsed.check_out
    elif type(parsed) is date:
        if stage == "check_in":
            arrival = parsed
        else:
            departure = parsed
    return {
        "arrival": arrival.isoformat() if arrival else None,
        "departure": departure.isoformat() if departure else None,
        "context_arrival": check_in.isoformat() if check_in else None,
        "context_departure": check_out.isoformat() if check_out else None,
        "input_stage": stage,
        "room_options": [offer["room"] for offer in state.get("choices", [])],
        "quoted_room": state.get("room"),
        "selected_room": result.get("room") if stage == "room" else None,
        "quote_decision": next(
            (event for _, event, target in decisions if target and event in {"approve_quote", "decline_quote"}), None,
        ),
        "delivery_choice": {"email": "Email", "phone": "SMS"}.get(result["stage"]) if stage == "delivery" else None,
        "contact_candidate": result.get("contact_candidate") if contact_entry else None,
        "guests": parsed if type(parsed) is int else None,
        "decisions": decisions,
        "stage": result["stage"],
        "chat": render_chat(result, overrides=overrides),
        "voice": voice,
        "voice_fallback": result["reply"],
    }
