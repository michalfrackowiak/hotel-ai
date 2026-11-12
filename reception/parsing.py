import re
from datetime import date, datetime, timedelta

from django.utils import timezone


NUMBER_WORDS = {
    "en": {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6},
    "pl": {"jeden": 1, "jedna": 1, "dwa": 2, "dwie": 2, "dwóch": 2, "trzy": 3, "cztery": 4, "pięć": 5, "sześć": 6},
}
DATE_FORMATS = {
    "en": ("%Y-%m-%d", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%B %d %Y"),
    "pl": ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"),
}
POLISH_MONTHS = (
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
)
RELATIVE_DATES = {
    "en": {"today": 0, "tomorrow": 1, "day after tomorrow": 2, "the day after tomorrow": 2},
    "pl": {"dzisiaj": 0, "dziś": 0, "jutro": 1, "pojutrze": 2},
}


def parse_stay_input(stage: str, text: str, language: str) -> date | int | None:
    if stage == "guests":
        number = re.fullmatch(
            r"([a-ząćęłńóśźż]+|[0-9]{1,5})(?:\s+(?:people|persons?|guests?|osob[ayę]|osób|gość|gości))?",
            text.casefold().rstrip(".!"),
        )
        if number is None:
            return None
        token = number[1]
        if token.isdecimal():
            return int(token)
        return NUMBER_WORDS[language].get(token)

    if stage in ("check_in", "check_out"):
        value = text.casefold().rstrip(".!")
        days = RELATIVE_DATES[language].get(value)
        if days is not None:
            return timezone.localdate() + timedelta(days=days)
        if language == "en":
            value = re.sub(r"(?<=[0-9])(?:st|nd|rd|th)\b", "", value)
        for fmt in DATE_FORMATS[language]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        if language == "pl":
            return _parse_polish_month_date(value)
    return None


def _parse_polish_month_date(text: str) -> date | None:
    match = re.fullmatch(r"([0-9]{1,2})\s+(\w+)\s+([0-9]{4})(?:\s+(?:r|roku))?", text)
    if match is None or match[2] not in POLISH_MONTHS:
        return None
    month = POLISH_MONTHS.index(match[2]) + 1
    try:
        return date(int(match[3]), month, int(match[1]))
    except ValueError:
        return None
