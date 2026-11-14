import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from reception.conversation import advance, new_conversation
from reception.parsing import parse_stay_input

from .models import PhoneCall


DRAFT_LIFETIME = timedelta(minutes=20)
TERMINAL_LIFETIME = timedelta(days=1)
MAX_MISSED_TURNS = 3
MAX_SPEECH_LENGTH = 500
TERMINAL_STAGES = ("completed", "ended")
STAY_STAGES = ("check_in", "check_out", "guests")
LANGUAGE_CHOICES = {"1": "en", "2": "pl"}
MENU = "Welcome to hotel reception. For English, press one."
MENU_RETRY = "Please choose a language. For English, press one."
COPY = {
    "en": {
        "check_in": "What date would you like to check in? Say the day, month and four-digit year, or say tomorrow.",
        "check_out": "What date will you check out? Say the day, month and four-digit year.",
        "email": "What is your email address? Say at and dot where they appear.",
        "email_retry": "I did not catch a valid email address. Please say it again, including at and dot.",
        "silence": "I didn't hear an answer. Please try again.",
        "ended": "This call has ended. Please call again to make a booking. Goodbye.",
    },
    "pl": {
        "check_in": "Kiedy chcesz się zameldować? Podaj dzień, miesiąc i pełny rok lub powiedz jutro.",
        "check_out": "Kiedy chcesz się wymeldować? Podaj dzień, miesiąc i pełny rok.",
        "email": "Jaki jest Twój adres e-mail? Wypowiedz słowa małpa i kropka w odpowiednich miejscach.",
        "email_retry": "Nie udało się rozpoznać adresu e-mail. Powtórz go, używając słów małpa i kropka.",
        "silence": "Nie usłyszałem odpowiedzi. Spróbuj ponownie.",
        "ended": "Połączenie zostało zakończone. Zadzwoń ponownie, aby dokonać rezerwacji. Do widzenia.",
    },
}
EMAIL_WORDS = {"en": ("at", "dot"), "pl": ("małpa", "kropka")}


@dataclass(frozen=True)
class CallReply:
    revision: str
    stage: str
    language: str
    reply: str
    reference: str | None = None


def is_call_sid(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"CA[0-9a-fA-F]{32}", value) is not None


def _canonical_call_sid(call_sid):
    if not is_call_sid(call_sid):
        raise ValueError("Invalid call SID")
    return "CA" + call_sid[2:].lower()


def _reply(state, missed_turns=0):
    stage = state["stage"]
    language = state["language"]
    reply = state["reply"]
    if missed_turns and stage != "language":
        reply = f"{COPY[language]['silence']} {reply}"
    return CallReply(state["revision"], stage, language, reply, state.get("reference"))


def _ended(language):
    return {"revision": "", "stage": "ended", "language": language, "reply": COPY[language]["ended"]}


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
            "state": {"revision": uuid4().hex, "stage": "language", "language": "en", "reply": MENU},
            "expires_at": now + DRAFT_LIFETIME,
        },
    )
    return _current_reply(call, now)


def _spoken_email(text, language):
    at, dot = EMAIL_WORDS[language]
    normalized = re.sub(rf"\s+{at}\s+", "@", text, flags=re.IGNORECASE)
    normalized = re.sub(rf"\s+{dot}\s+", ".", normalized, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", normalized).removesuffix(".") if "@" in normalized else text


def _spoken_reply(previous, next_state):
    stage = next_state["stage"]
    copy = COPY[next_state["language"]]
    if stage == "email":
        next_state["reply"] = copy["email_retry" if previous["stage"] == "email" else "email"]
    elif stage in ("check_in", "check_out"):
        # An unavailable or cancelled stay returns a specific check-in prompt from the booking flow.
        if stage == "check_out" or previous["stage"] == "check_in":
            next_state["reply"] = copy[stage]
    return next_state


def _missed_turn(call, now):
    state = call.state
    missed_turns = call.missed_turns + 1
    if missed_turns >= MAX_MISSED_TURNS:
        return _save(call, _ended(state["language"]), now)
    # Keep the original prompt so repeated misses do not accumulate the retry prefix.
    updated = {**state, "revision": uuid4().hex}
    if state["stage"] == "language":
        updated["reply"] = MENU_RETRY
    return _save(call, updated, now, missed_turns)


def advance_call(call_sid: str, revision: str, *, digit: str = "", speech: str = "") -> CallReply:
    call_sid = _canonical_call_sid(call_sid)
    preview = PhoneCall.objects.filter(pk=call_sid).values_list("state", flat=True).first()
    if preview is None:
        return _reply(_ended("en"))

    speech = speech.strip()
    parsed = None
    if preview["revision"] == revision and 1 <= len(speech) <= MAX_SPEECH_LENGTH:
        stage = preview["stage"]
        if stage in (*STAY_STAGES, "confirm"):
            speech = speech.rstrip(".!。！").strip()
        if stage == "email":
            speech = _spoken_email(speech, preview["language"])
        if stage in STAY_STAGES:
            parsed = parse_stay_input(stage, speech, preview["language"])

    # Checking the revision again under SQLite's write lock keeps booking and the receipt atomic.
    with transaction.atomic():
        call = PhoneCall.objects.filter(pk=call_sid).first()
        if call is None:
            return _reply(_ended(preview["language"]))
        now = timezone.now()
        current = _current_reply(call, now)
        if current.stage in TERMINAL_STAGES or current.revision != revision:
            return current
        if current.stage == "language":
            language = LANGUAGE_CHOICES.get(digit)
            if language is None:
                return _missed_turn(call, now)
            state = new_conversation(language)
            state["reply"] = COPY[language]["check_in"]
            return _save(call, state, now)
        if not 1 <= len(speech) <= MAX_SPEECH_LENGTH:
            return _missed_turn(call, now)
        return _save(call, _spoken_reply(call.state, advance(call.state, speech, parsed)), now)


@transaction.atomic
def expire_calls() -> tuple[int, int]:
    """Redact expired drafts and prune terminal calls after their retry window."""
    now = timezone.now()
    expired = PhoneCall.objects.filter(expires_at__lte=now)
    deleted, _ = expired.filter(state__stage__in=TERMINAL_STAGES).delete()
    redacted = 0
    for language in LANGUAGE_CHOICES.values():
        redacted += expired.filter(state__language=language).update(
            state=_ended(language), missed_turns=0, expires_at=now + TERMINAL_LIFETIME,
        )
    return redacted, deleted
