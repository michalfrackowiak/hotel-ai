import re
from datetime import date

from django.utils import timezone

from .contacts import PHONE_DIGITS, parse_email, parse_phone
from .conversation import _answer, _room_choice, selects_english
from .models import ParserSample
from .parser_training import learned_result, rule_key
from .parsing import StayDates


MAX_PARSER_SAMPLES = 1000
REVIEW_STAGES = {"check_in", "check_out", "guests", "room", "email", "phone"}
CONTACT_STAGES = {"email", "phone"}
CONTACT_DIGITS = re.compile(r"(?<![\w/.-])\+?\d(?:[\s().-]*\d){7,14}(?![\w/.-])")
CALENDAR_DIGITS = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b")


def _review_text_allowed(text):
    if not isinstance(text, str) or len(text) > 500 or not text.isprintable():
        return False
    if "@" in text or parse_email(text):
        return False
    digits = re.sub(
        r"\b[^\W\d_]+\b",
        lambda match: PHONE_DIGITS.get(match[0], match[0]),
        CALENDAR_DIGITS.sub(" ", text.casefold()),
    )
    return not CONTACT_DIGITS.search(digits)


def delete_expired_samples():
    return ParserSample.objects.exclude(pk__in=ParserSample.objects.unexpired().values("pk")).delete()[0]


def _extracted_value(state, text, parsed):
    stage = state["stage"]
    if stage == "room":
        return _room_choice(state, _answer(text))
    if stage == "email":
        return parse_email(text)
    if stage == "phone":
        return parse_phone(text)
    return parsed


def _result_text(state, value):
    stage = state["stage"]
    if value is None:
        return "", False
    if isinstance(value, StayDates):
        return (
            f"{value.check_in.isoformat()} / {value.check_out.isoformat()}",
            timezone.localdate() <= value.check_in < value.check_out,
        )
    if type(value) is date:
        minimum = date.fromisoformat(state["check_in"]) if stage == "check_out" else timezone.localdate()
        valid = value > minimum if stage == "check_out" else value >= minimum
        return value.isoformat(), valid
    if type(value) is int:
        return str(value), 1 <= value <= 32767
    if stage == "room":
        return value["room"], True
    return value, True


def review_booking_input(state, text, parsed, *, source):
    """Record and resolve the guest turn under the caller's revision write lock."""
    stage, language = state["stage"], state["language"]
    if stage == "welcome" and parsed is not None:
        state = {**state, "stage": "check_in"}
        stage = "check_in"
    if stage not in REVIEW_STAGES or state.get("flow") != "neptun":
        return parsed, False
    if stage == "check_in" and language == "pl" and selects_english(text):
        return parsed, False
    original = _extracted_value(state, text, parsed)
    if stage not in CONTACT_STAGES and not _review_text_allowed(text):
        return original, True
    corrected = learned_result(state, text, original, source=source)
    result, succeeded = _result_text(state, original)
    delete_expired_samples()
    samples = ParserSample.objects
    if samples.count() >= MAX_PARSER_SAMPLES:
        oldest = samples.filter(corrected_result="").order_by("created_at", "pk").values_list("pk", flat=True).first()
        if oldest is not None:
            samples.filter(pk=oldest).delete()
    if samples.count() < MAX_PARSER_SAMPLES:
        samples.create(
            language=language, stage=stage, source=source, text=text,
            parsed_result=result, succeeded=succeeded, rule_key=rule_key(language, stage, text),
        )
    return (original if corrected is None else corrected), True
