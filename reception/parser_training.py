import re
from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.crypto import salted_hmac

from bookings.models import Room

from .contacts import parse_email, parse_phone
from .parsing import NUMBER_WORDS, ORDINALS, RELATIVE_DATES, StayDates, _parse_date, parse_stay_input


STAY_STAGES = {"check_in", "check_out", "guests"}
CONTACT_STAGES = {"email", "phone"}
MAX_SAMPLE_TEXT = 500
ISO_DATE_PAIR = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})\s*/\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")


def _normalized(text, stage):
    if stage in CONTACT_STAGES:
        return text.strip()
    return " ".join(text.casefold().split()).rstrip(".!?。！？").rstrip()


def rule_key(language, stage, text):
    # A keyed digest keeps the lookup index from exposing easily guessed contacts.
    return salted_hmac(
        "reception.parser-training", f"{language}\0{stage}\0{_normalized(text, stage)}", algorithm="sha256",
    ).hexdigest()


def _same_email(text, corrected):
    candidate = parse_email(text)
    if candidate is None:
        # Accept a spoken separator alias without inventing address characters.
        spoken = re.sub(r"(?<=\s)(?:monkey|at sign)(?=\s)", "at", text, flags=re.IGNORECASE)
        candidate = parse_email(spoken)
    return corrected if candidate == corrected else None


def _same_phone(text, corrected):
    candidate = parse_phone(text)
    if candidate is None:
        spoken = re.sub(r"^(?:plus sign|plus symbol|znak plusa)\s+", "+ ", text.strip(), flags=re.IGNORECASE)
        candidate = parse_phone(spoken)
    return corrected if candidate == corrected else None


def _contact_correction(stage, text, corrected):
    if stage == "email":
        # Require a structural address rather than a second interpretation of speech.
        if "@" in corrected and parse_email(corrected) == corrected:
            return _same_email(text, corrected)
    else:
        candidate = parse_phone(corrected)
        if candidate is not None:
            return _same_phone(text, candidate)
    return None


def _stay_value(state, text):
    stage = state["stage"]
    today = timezone.localdate()
    check_in = date.fromisoformat(state["check_in"]) if stage == "check_out" else None
    if stage == "check_in" and (match := ISO_DATE_PAIR.fullmatch(text)):
        try:
            value = StayDates(*(date.fromisoformat(part) for part in match.groups()))
        except ValueError:
            return None
    else:
        value = parse_stay_input(stage, text, state["language"], check_in=check_in)
    if stage == "check_in":
        if isinstance(value, StayDates):
            return value if today <= value.check_in < value.check_out else None
        return value if type(value) is date and value >= today else None
    if stage == "check_out":
        return value if type(value) is date and value > check_in else None
    return value if type(value) is int and 1 <= value <= 32767 else None


def _room_value(state, text):
    from .conversation import _answer, _room_choice

    return _room_choice(state, _answer(text))


def _reviewed_room_text(language, corrected, *, room_codes=None):
    from .conversation import _answer, _room_option_number

    label = _answer(corrected)
    if (number := _room_option_number(language, label)) is not None:
        if number < 1:
            return None
        return corrected
    prefix = "room " if language == "en" else "pokój "
    room = label.removeprefix(prefix)
    if room_codes is not None:
        return corrected if room in room_codes else None
    if room and Room.objects.filter(code__iexact=room).exists():
        return corrected
    return None


def _text_ok(text):
    return isinstance(text, str) and 0 < len(text) <= MAX_SAMPLE_TEXT and text.isprintable() and bool(text.strip())


def _protected_literal(stage, text, language, source, *, room_codes=None):
    label = _normalized(text, stage)
    plain_count = bool(re.fullmatch(r"[0-9]{1,5}", label) or label in NUMBER_WORDS[language])
    if stage == "guests":
        return plain_count or (source == "chat" and parse_stay_input(stage, label, language) is not None)
    if stage in ("check_in", "check_out"):
        spelled_date = label in RELATIVE_DATES[language] or label in ORDINALS[language]
        numeric_date = re.fullmatch(r"[0-9]{1,4}(?:[./-][0-9]{1,4}){1,2}|[0-9]{1,2}(?:st|nd|rd|th)", label)
        if spelled_date or numeric_date or plain_count or ISO_DATE_PAIR.fullmatch(label):
            return True
        if source == "chat":
            today = timezone.localdate()
            try:
                return _parse_date(label, language, today if stage == "check_out" else None, today) is not None
            except (OverflowError, ValueError):
                return False
    if stage == "room":
        if source == "chat":
            return _reviewed_room_text(language, text, room_codes=room_codes) is not None
        return plain_count or label in ORDINALS[language]
    return False


def validate_correction(stage, text, corrected_result, *, language="en", source="voice", room_codes=None):
    """Return the reviewed rule text or raise a validation error for an unsafe correction."""
    if corrected_result == "":
        return ""
    if not _text_ok(text) or not _text_ok(corrected_result) or language not in ("pl", "en"):
        raise ValidationError("Enter a single line of text for a supported booking stage.")
    corrected = _normalized(corrected_result, stage)
    if stage in CONTACT_STAGES:
        if contact := _contact_correction(stage, text, corrected):
            return contact
        raise ValidationError("The correction must preserve the email address characters or phone digits.")
    if _protected_literal(stage, text, language, source, room_codes=room_codes):
        raise ValidationError("This reply is a direct booking value. Correct a misrecognized or ambiguous phrase instead.")
    if stage in STAY_STAGES:
        state = {"stage": stage, "language": language, "check_in": timezone.localdate().isoformat()}
        if _stay_value(state, corrected) is not None:
            return corrected
        raise ValidationError("Enter a valid date, stay length, or guest count for this prompt.")
    if stage == "room" and _reviewed_room_text(language, corrected, room_codes=room_codes):
        return corrected
    raise ValidationError("Enter an existing room code or a room option number.")


def learned_result(state, text, parsed, *, source):
    """Resolve one exact reviewed rule in the current stay and offer context."""
    from .models import ParserSample

    stage, language = state["stage"], state["language"]
    if stage not in (*STAY_STAGES, *CONTACT_STAGES, "room") or not _text_ok(text) or not _normalized(text, stage):
        return None
    corrections = list(
        ParserSample.objects.enabled_corrections()
        .filter(language=language, stage=stage, source=source, rule_key=rule_key(language, stage, text))
        .order_by().values_list("corrected_result", flat=True).distinct()[:2]
    )
    if len(corrections) != 1:
        return None
    return apply_reviewed_correction(state, text, parsed, corrections[0], source=source)


def apply_reviewed_correction(state, text, parsed, corrected, *, source, room_codes=None):
    """Apply one reviewed rule against the current stay and offer context."""
    stage, language = state["stage"], state["language"]
    if not _text_ok(corrected) or _protected_literal(stage, text, language, source, room_codes=room_codes):
        return None
    corrected = corrected.strip()
    if stage in CONTACT_STAGES:
        learned = _contact_correction(stage, text, corrected)
    elif stage == "room":
        learned = _room_value(state, corrected)
    else:
        learned = _stay_value(state, corrected)
    return learned if learned != parsed else None
