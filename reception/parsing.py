import re
from calendar import monthrange
from datetime import date, datetime, timedelta

from django.utils import timezone


NUMBER_WORDS = {
    "en": dict(zip("zero one two three four five six seven eight nine ten eleven twelve".split(), range(13))),
    "pl": dict(zip("zero jeden dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć jedenaście dwanaście".split(), range(13))),
}
NUMBER_WORDS["en"].update(a=1, an=1)
NUMBER_WORDS["pl"].update(jedna=1, jedną=1, dwie=2, dwóch=2)
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
RELATIVE_OFFSETS = {
    "en": r"in ([0-9]{1,5}|[a-z]+) (day|days|week|weeks)",
    "pl": r"za ([0-9]{1,5}|[a-ząćęłńóśźż]+) (dzień|dni|tydzień|tygodnie|tygodni)",
}
WEEK_UNITS = {"week", "weeks", "tydzień", "tygodnie", "tygodni"}
DURATIONS = {
    "en": r"(?:for )?(?:([0-9]{1,5}|[a-z]+) )?(night|nights|day|days|week|weeks|month|months)",
    "pl": r"(?:na |przez )?(?:([0-9]{1,5}|[a-ząćęłńóśźż]+) )?(noc|noce|nocy|dzień|dni|tydzień|tygodnie|tygodni|miesiąc|miesiące|miesięcy)",
}
MONTH_UNITS = {"month", "months", "miesiąc", "miesiące", "miesięcy"}
ENGLISH_MONTHS = "january february march april may june july august september october november december".split()
MONTHS = {
    "en": {name: number for number, name in enumerate(ENGLISH_MONTHS, 1)},
    "pl": {name: number for number, name in enumerate(POLISH_MONTHS, 1)},
}
EN_ORDINALS = (
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth"
).split()
PL_ORDINALS = (
    "pierwszego drugiego trzeciego czwartego piątego szóstego siódmego ósmego dziewiątego dziesiątego "
    "jedenastego dwunastego trzynastego czternastego piętnastego szesnastego siedemnastego "
    "osiemnastego dziewiętnastego dwudziestego"
).split()
ORDINALS = {
    "en": dict(zip(
        EN_ORDINALS + [f"twenty {unit}" for unit in EN_ORDINALS[:9]] + ["thirtieth", "thirty first"],
        range(1, 32),
    )),
    "pl": dict(zip(
        PL_ORDINALS + [f"dwudziestego {unit}" for unit in PL_ORDINALS[:9]]
        + ["trzydziestego", "trzydziestego pierwszego"],
        range(1, 32),
    )),
}


def _number(token, language):
    return int(token) if token.isdecimal() else NUMBER_WORDS[language].get(token)


def parse_stay_input(stage: str, text: str, language: str, *, check_in: date | None = None) -> date | int | None:
    value = " ".join(text.casefold().strip().rstrip(".!?。！？").split())
    if stage == "guests":
        number = re.fullmatch(
            r"([a-ząćęłńóśźż]+|[0-9]{1,5})(?:\s+(?:people|persons?|guests?|osob[ayę]|osób|gość|gości))?",
            value,
        )
        return _number(number[1], language) if number else None

    if stage in ("check_in", "check_out"):
        try:
            return _parse_date(value, language, check_in if stage == "check_out" else None)
        except (OverflowError, ValueError):
            return None
    return None


def _parse_date(value, language, check_in):
    today = timezone.localdate()
    days = RELATIVE_DATES[language].get(value)
    offset = re.fullmatch(RELATIVE_OFFSETS[language], value)
    if offset and (amount := _number(offset[1], language)) is not None:
        days = amount * (7 if offset[2] in WEEK_UNITS else 1)
    if days is not None:
        return today + timedelta(days=days)
    if check_in and value not in {"a", "an"}:
        nights = _number(value, language) if len(value) <= 12 else None
        if nights is not None:
            return check_in + timedelta(days=nights) if nights else None
    duration = re.fullmatch(DURATIONS[language], value) if check_in else None
    if duration:
        amount = _number(duration[1], language) if duration[1] else 1
        if amount:
            return _add_duration(check_in, amount, duration[2])
        return None
    if language == "en":
        value = re.sub(r"(?<=[0-9])(?:st|nd|rd|th)\b", "", value)
        value = value.removeprefix("the ").replace(" of ", " ")
    for fmt in DATE_FORMATS[language]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return _calendar_date(value, language, today)


def _add_duration(check_in, amount, unit):
    if unit in MONTH_UNITS:
        year, month_index = divmod(check_in.month - 1 + amount, 12)
        year += check_in.year
        month = month_index + 1
        return date(year, month, min(check_in.day, monthrange(year, month)[1]))
    return check_in + timedelta(days=amount * (7 if unit in WEEK_UNITS else 1))


def _calendar_date(value, language, anchor):
    parts = re.fullmatch(r"([0-9]{1,2})[./]([0-9]{1,2})", value)
    if parts:
        first, second = map(int, parts.groups())
        month, day = (first, second) if language == "en" else (second, first)
        return date(anchor.year + (month < anchor.month), month, day)

    year = re.search(r"\s+([0-9]{4})(?:\s+(?:r|roku))?$", value)
    if year:
        value = value[:year.start()].rstrip(", ")
    tokens = value.replace("-", " ").split()
    months = MONTHS[language]
    mentioned_months = [months[token] for token in tokens if token in months]
    if len(mentioned_months) > 1:
        return None
    month = mentioned_months[0] if mentioned_months else None
    day_text = " ".join(token for token in tokens if token not in months)
    day = int(day_text) if re.fullmatch(r"[0-9]{1,2}", day_text) else ORDINALS[language].get(day_text)
    if day is None or (year and month is None):
        return None
    month = month or anchor.month
    return date(int(year[1]) if year else anchor.year + (month < anchor.month), month, day)
