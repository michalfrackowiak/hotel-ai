import re
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from reception.contacts import parse_email
from reception.conversation import COPY as BOOKING_COPY
from reception.conversation import TERMINAL_STAGES as CONVERSATION_TERMINAL_STAGES
from reception.conversation import WELCOME_PARTS, advance, new_conversation, selects_english
from reception.parser_review import review_booking_input
from reception.parsing import StayDates, parse_stay_input
from reception.receipt import DELIVERY_PENDING, refresh_receipt
from reception.scenario import PHONE_COPY as COPY
from reception.scenario import voice_segments

from .models import PhoneCall


DRAFT_LIFETIME = timedelta(minutes=20)
TERMINAL_LIFETIME = timedelta(days=1)
MAX_MISSED_TURNS = 3
MAX_SPEECH_LENGTH = 500
TERMINAL_STAGES = (*CONVERSATION_TERMINAL_STAGES, "ended")
STAY_STAGES = ("check_in", "check_out", "guests")
MENU = WELCOME_PARTS[0][1]


def staff_transfer_destination():
    number = settings.HOTEL_PHONE_RECEPTION_NUMBER
    return number if re.fullmatch(r"\+[1-9][0-9]{7,14}", number) else None


@dataclass(frozen=True)
class CallReply:
    revision: str
    stage: str
    language: str
    reply: str
    reference: str | None = None
    parts: tuple[dict, ...] = ()


def is_call_sid(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"CA[0-9a-fA-F]{32}", value) is not None


def _canonical_call_sid(call_sid):
    if not is_call_sid(call_sid):
        raise ValueError("Invalid call SID")
    return "CA" + call_sid[2:].lower()


def _literal_part(text, language):
    return {"text": text, "language": language, "seconds": 0, "listen": False}


def phone_prompt_parts(key, language):
    state = {"prompt_key": f"phone_{key}", "language": language, "reply": COPY[language][key]}
    return voice_segments(state)


def _reply(state, missed_turns=0):
    stage = state["stage"]
    language = state["language"]
    reply = state["reply"]
    prompt_state = state
    if stage == "check_in" and not state.get("prompt_key") and reply == BOOKING_COPY[language]["check_in"]:
        prompt_state = {**state, "prompt_key": "check_in"}
    parts = voice_segments(prompt_state)
    if missed_turns and stage != "language":
        prefix = phone_prompt_parts("silence", language)
        if parts or prefix:
            parts = (prefix or [_literal_part(COPY[language]["silence"], language)]) + (
                parts or [_literal_part(reply, language)]
            )
        reply = f"{COPY[language]['silence']} {reply}"
    return CallReply(state["revision"], stage, language, reply, state.get("reference"), tuple(parts))


def _ended(language):
    return {
        "revision": "", "stage": "ended", "language": language,
        "reply": COPY[language]["ended"], "prompt_key": "phone_ended",
    }


def _save(call, state, now, missed_turns=0):
    call.state = state
    call.missed_turns = missed_turns
    lifetime = TERMINAL_LIFETIME if state["stage"] in TERMINAL_STAGES else DRAFT_LIFETIME
    call.expires_at = now + lifetime
    call.save(update_fields=("state", "missed_turns", "expires_at"))
    return _reply(state, missed_turns)


def _current_reply(call, now):
    if call.state["stage"] not in TERMINAL_STAGES and call.expires_at <= now:
        return _save(call, _ended(call.state["language"]), now)
    return _reply(call.state, call.missed_turns)


@transaction.atomic
def start_call(call_sid: str) -> CallReply:
    now = timezone.now()
    call, _ = PhoneCall.objects.get_or_create(
        call_sid=_canonical_call_sid(call_sid),
        defaults={
            "state": {
                "revision": uuid4().hex, "stage": "language", "language": "pl", "reply": MENU,
                "prompt_key": "welcome",
            },
            "expires_at": now + DRAFT_LIFETIME,
        },
    )
    return _current_reply(call, now)


def _missed_turn(call, now):
    state = call.state
    missed_turns = call.missed_turns + 1
    if missed_turns >= MAX_MISSED_TURNS:
        return _save(call, _ended(state["language"]), now)
    # Keep the original prompt so repeated misses do not accumulate the retry prefix.
    updated = {**state, "revision": uuid4().hex}
    return _save(call, updated, now, missed_turns)


def _arrive_from_menu(digit, speech):
    speech = speech.rstrip(".!。！").strip()
    if digit == "2" or selects_english(speech):
        return new_conversation("en", "check_in")
    state = new_conversation("pl", "check_in")
    parsed = parse_stay_input("check_in", speech, "pl")
    arrival = parsed.check_in if isinstance(parsed, StayDates) else parsed
    if arrival is not None and arrival >= timezone.localdate():
        return advance(state, speech, parsed, voice=True)
    return state


@transaction.atomic
def advance_call(call_sid: str, revision: str, *, digit: str = "", speech: str = "") -> CallReply:
    # Hold SQLite's write lock through revision validation and booking.
    call = PhoneCall.objects.filter(pk=_canonical_call_sid(call_sid)).first()
    if call is None:
        return _reply(_ended("pl"))
    now = timezone.now()
    current = _current_reply(call, now)
    if current.stage in TERMINAL_STAGES or current.revision != revision:
        return current
    if current.stage == DELIVERY_PENDING:
        state = refresh_receipt(call.state)
        return current if state == call.state else _save(call, state, now)

    speech = speech.strip()
    if len(speech) > MAX_SPEECH_LENGTH:
        speech = ""
    if current.stage == "language":
        # A caller may have heard the version of the menu in use before deployment.
        if current.language == "en" and digit in ("1", "2"):
            return _save(call, new_conversation("en" if digit == "1" else "pl", "check_in"), now)
        return _save(call, _arrive_from_menu(digit, speech), now)

    message = digit if current.stage == "room" and digit else speech
    if current.stage in (*STAY_STAGES, "confirm"):
        message = message.rstrip(".!。！").strip()
    if not message and current.stage != "check_in":
        return _missed_turn(call, now)
    if current.stage == "email" and call.state.get("flow") != "neptun":
        message = parse_email(message) or message
    parsed = None
    if current.stage in STAY_STAGES:
        check_in = call.state.get("check_in")
        parsed = parse_stay_input(
            current.stage, message, current.language,
            check_in=date.fromisoformat(check_in) if check_in else None,
        )
    input_parsed = False
    if message and settings.HOTEL_PARSER_TRAINING_ENABLED:
        parsed, input_parsed = review_booking_input(call.state, message, parsed, source="voice")
    return _save(call, advance(call.state, message, parsed, voice=True, input_parsed=input_parsed), now)


@transaction.atomic
def finish_handover(call_sid: str, revision: str, *, connected: bool) -> CallReply:
    call = PhoneCall.objects.filter(pk=_canonical_call_sid(call_sid)).first()
    if call is None:
        return _reply(_ended("pl"))
    state = call.state
    if state["revision"] != revision or state["stage"] not in ("handover", "ended"):
        return _reply(_ended(state["language"]))
    if state["stage"] == "ended":
        return _reply(state)
    language = state["language"]
    return _save(call, {
        "revision": revision, "stage": "ended", "language": language,
        "reply": "" if connected else COPY[language]["handover_unavailable"],
        **({} if connected else {"prompt_key": "phone_handover_unavailable"}),
    }, timezone.now())


@transaction.atomic
def expire_calls() -> tuple[int, int]:
    """Redact expired drafts and prune terminal calls after their retry window."""
    now = timezone.now()
    expired = PhoneCall.objects.filter(expires_at__lte=now)
    deleted, _ = expired.filter(state__stage__in=TERMINAL_STAGES).delete()
    redacted = 0
    for language in ("en", "pl"):
        redacted += expired.filter(state__language=language).update(
            state=_ended(language), missed_turns=0, expires_at=now + TERMINAL_LIFETIME,
        )
    return redacted, deleted
