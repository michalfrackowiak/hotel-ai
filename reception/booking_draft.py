"""Project and revise the guest's unconfirmed stay."""

import re
from datetime import date, timedelta

from django.utils import timezone

from .conversation import new_conversation
from .parsing import StayDates
from .scenario import set_prompt
from .scenario_dispatch import advance_web_turn
from .scenario_pinning import carry_web_scenario


DATE_STAGES = {"welcome", "check_in", "check_out"}
EDIT_STAGES = {"check_out", "guests", "room", "confirm", "delivery", "email", "phone", "contact_confirm"}
DRAFT_FIELDS = ("check_in", "check_out", "guests", "room", "total_price")
LAST_ARRIVAL = date.max - timedelta(days=1)


def edit_actions(state):
    if state.get("flow") != "neptun" or state["stage"] not in EDIT_STAGES:
        return []
    actions = ["dates"] if "check_in" in state else []
    if "check_out" in state and "guests" in state:
        actions.append("guests")
    if "room" in state:
        actions.append("room")
    return actions


def summary(state):
    actions = edit_actions(state)
    if not actions:
        return None
    return {**{field: state[field] for field in DRAFT_FIELDS if field in state}, "edits": actions}


def rewind(state, action, *, voice=False):
    draft = carry_web_scenario(state, new_conversation(state["language"], "check_in"))
    if action == "dates":
        return draft
    draft.update(stage="guests", check_in=state["check_in"], check_out=state["check_out"])
    set_prompt(draft, "guests")
    if action == "guests":
        return draft
    return advance_web_turn(draft, "", state["guests"], voice=voice)


def parse_selection(selection):
    if not isinstance(selection, dict) or selection.keys() not in (
        {"check_in"}, {"check_out"}, {"check_in", "check_out"},
    ):
        return None
    dates = {}
    for field, value in selection.items():
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return None
        try:
            dates[field] = date.fromisoformat(value)
        except ValueError:
            return None
    return dates


def date_picker(state, *, require_range):
    if state.get("flow") != "neptun" or state["stage"] not in DATE_STAGES:
        return None
    today = timezone.localdate()
    if state["stage"] == "check_out":
        arrival = date.fromisoformat(state["check_in"])
        if not today <= arrival <= LAST_ARRIVAL:
            return None
        return {"departure_min": (arrival + timedelta(days=1)).isoformat(), "departure_required": True}
    return {"arrival_min": today.isoformat(), "departure_required": require_range}


def selected_dates(state, selection, *, require_range):
    picker = date_picker(state, require_range=require_range)
    if picker is None:
        return None
    arrival = selection.get("check_in")
    departure = selection.get("check_out")
    if state["stage"] == "check_out":
        return departure if arrival is None and departure and departure.isoformat() >= picker["departure_min"] else None
    if arrival is None or not date.fromisoformat(picker["arrival_min"]) <= arrival <= LAST_ARRIVAL:
        return None
    if departure is None:
        return None if require_range else arrival
    if departure > arrival:
        return StayDates(arrival, departure)
