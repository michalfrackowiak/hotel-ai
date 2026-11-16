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
SPOKEN_DIGITS = {
    "en": "zero one two three four five six seven eight nine".split(),
    "pl": "zero jeden dwa trzy cztery pięć sześć siedem osiem dziewięć".split(),
}
SPOKEN_SYMBOLS = {
    "en": {
        "@": "at sign", ".": "dot", "+": "plus", "-": "hyphen", "_": "underscore",
        "!": "exclamation mark", "#": "hash", "$": "dollar sign", "%": "percent sign",
        "&": "ampersand", "'": "apostrophe", "*": "asterisk", "/": "slash",
        "=": "equals sign", "?": "question mark", "^": "caret", "`": "backtick",
        "{": "left brace", "}": "right brace", "|": "vertical bar", "~": "tilde",
        '"': "quotation mark", "\\": "backslash", "[": "left bracket", "]": "right bracket",
        ":": "colon", " ": "space", ",": "comma", ";": "semicolon",
        "<": "less than sign", ">": "greater than sign", "(": "left parenthesis", ")": "right parenthesis",
    },
    "pl": {
        "@": "małpa", ".": "kropka", "+": "plus", "-": "myślnik", "_": "podkreślenie",
        "!": "wykrzyknik", "#": "kratka", "$": "znak dolara", "%": "procent",
        "&": "ampersand", "'": "apostrof", "*": "gwiazdka", "/": "ukośnik",
        "=": "znak równości", "?": "znak zapytania", "^": "daszek", "`": "grawis",
        "{": "lewy nawias klamrowy", "}": "prawy nawias klamrowy", "|": "pionowa kreska", "~": "tylda",
        '"': "cudzysłów", "\\": "ukośnik wsteczny", "[": "lewy nawias kwadratowy", "]": "prawy nawias kwadratowy",
        ":": "dwukropek", " ": "spacja", ",": "przecinek", ";": "średnik",
        "<": "znak mniejszości", ">": "znak większości", "(": "lewy nawias okrągły", ")": "prawy nawias okrągły",
    },
}


def speak_contact(contact, language):
    symbols = SPOKEN_SYMBOLS[language]
    digits = SPOKEN_DIGITS[language]
    uppercase = "capital" if language == "en" else "wielka litera"
    words = []
    for character in contact:
        if character in symbols:
            words.append(symbols[character])
        elif character in "0123456789":
            words.append(digits[int(character)])
        elif character.isupper():
            words.append(f"{uppercase} {character}")
        else:
            words.append(character)
    return ", ".join(words)


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
