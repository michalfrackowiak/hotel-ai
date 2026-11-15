import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from bookings.services import BookingError, normalize_phone

from .parsing import NUMBER_WORDS


EMAIL_SYMBOLS = {
    "at": "@", "małpa": "@", "małpka": "@", "dot": ".", "kropka": ".", "plus": "+",
    "hyphen": "-", "dash": "-", "myślnik": "-", "underscore": "_",
}
PHONE_DIGITS = {
    **{
        word: str(number)
        for words in NUMBER_WORDS.values()
        for word, number in words.items()
        if number < 10 and word not in {"a", "an"}
    },
    "oh": "0",
}


def parse_email(message):
    value = message.strip().rstrip(".")
    if re.search(r"\s", value):
        value = value.casefold().replace("podkreślenie", "underscore")
        tokens = value.split()
        if not any(EMAIL_SYMBOLS.get(token) == "@" or "@" in token for token in tokens):
            return None
        value = "".join(EMAIL_SYMBOLS.get(token, PHONE_DIGITS.get(token, token)) for token in tokens)
    if len(value) > 254 or not value.isprintable():
        return None
    try:
        validate_email(value)
    except ValidationError:
        return None
    return value


def parse_phone(message):
    value = message.casefold().strip().rstrip(".!?。！？")
    value = re.sub(r"^plus\s+", "+", value)
    value = re.sub(
        r"\b([^\W\d_]+)\b",
        lambda match: PHONE_DIGITS.get(match[0], match[0]),
        value,
    )
    try:
        return normalize_phone(value)
    except BookingError:
        return None
