import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone


NUMBER_WORDS = {
    "en": dict(zip("zero one two three four five six seven eight nine ten eleven twelve".split(), range(13))),
    "pl": dict(zip("zero jeden dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć jedenaście dwanaście".split(), range(13))),
}
NUMBER_WORDS["en"].update(a=1, an=1)
NUMBER_WORDS["pl"].update(jedna=1, jedną=1, dwie=2, dwóch=2)
DATE_FORMATS = {
    "en": ("%d %B %Y", "%B %d, %Y", "%B %d %Y"),
    "pl": (),
}
POLISH_MONTHS = (
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
)
RELATIVE_DATES = {
    "en": {"today": 0, "tomorrow": 1, "day after tomorrow": 2, "the day after tomorrow": 2},
    "pl": {"dzisiaj": 0, "dziś": 0, "jutro": 1, "jutra": 1, "pojutrze": 2, "pojutrza": 2},
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
RANGE_LINK = {"en": r"to|until|through", "pl": r"do"}
DURATION_START = {"en": r"for", "pl": r"na|przez"}
AMBIGUITY_MARKERS = {
    "en": re.compile(r"\b(?:or|not|never|instead|maybe|perhaps)\b|n['’]t\b"),
    "pl": re.compile(r"\b(?:lub|albo|bądź|nie|zamiast|może)\b"),
}
DATE_CONTEXT = {
    "check_in": {
        "en": re.compile(r"\b(?:from|arriv\w*|check(?:ing)?[ -]?in|start\w*)\b"),
        "pl": re.compile(r"\b(?:od|przyjazd\w*|przyjad\w*|przyjed\w*|zameld\w*|zakwater\w*)\b"),
    },
    "check_out": {
        "en": re.compile(r"\b(?:until|check(?:ing)?[ -]?out|depart\w*|leav\w*|stay\w*)\b"),
        "pl": re.compile(r"\b(?:do|wyjazd\w*|wyjad\w*|wyjed\w*|wymeld\w*|pobyt\w*|zost\w*)\b"),
    },
}
DATE_FILLERS = {
    "en": re.compile(r"\b(?:uh|um|erm|hmm|on|the|please|thanks|thank you)\b"),
    "pl": re.compile(r"\b(?:yyy|hmm|proszę|dzięki|dziękuję)\b"),
}


@dataclass(frozen=True)
class StayDates:
    check_in: date
    check_out: date


def _alternatives(values):
    return "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))


def _date_pattern(language):
    months = _alternatives(MONTHS[language])
    ordinals = _alternatives(ORDINALS[language]).replace(r"\ ", "[- ]")
    relative = _alternatives(RELATIVE_DATES[language])
    # Keep malformed numeric candidates visible so an invalid arrival does not
    # disappear from a range and leave its departure looking like a single date.
    numeric = r"[0-9]+[./-][0-9]+[./-][0-9]+|[0-9]{1,4}[./-][0-9]{1,2}"
    if language == "en":
        ordinal = r"[0-9]{1,2}(?:st|nd|rd|th)"
        day = rf"(?:{ordinal}|[0-9]{{1,2}}|{ordinals})"
        named = rf"(?:the )?(?:(?:{months}) {day}|{day} (?:of )?(?:{months}))(?:,? [0-9]{{4}})?"
    else:
        day = rf"(?:[0-9]{{1,2}}|{ordinals})"
        named = rf"{day} (?:{months})(?: [0-9]{{4}}(?: r\.?| roku)?)?"
        ordinal = ordinals
    return re.compile(rf"(?<![\w./-])(?:{numeric}|{named}|{relative}|{RELATIVE_OFFSETS[language]}|{ordinal})(?![\w/-]|\.[0-9])")


DATE_FRAGMENTS = {language: _date_pattern(language) for language in MONTHS}
DURATION_FRAGMENTS = {
    language: re.compile(rf"(?<![\w+/.-]){pattern}(?![\w/-]|\.[0-9])")
    for language, pattern in DURATIONS.items()
}


def _number(token, language):
    return int(token) if token.isdecimal() else NUMBER_WORDS[language].get(token)


def parse_stay_input(
    stage: str, text: str, language: str, *, check_in: date | None = None, today: date | None = None,
) -> StayDates | date | int | None:
    value = " ".join(text.casefold().strip().rstrip(".!?。！？").split())
    if stage == "guests":
        number = re.fullmatch(
            r"([a-ząćęłńóśźż]+|[0-9]{1,5})(?:\s+(?:people|persons?|guests?|osob[ayę]|osób|gość|gości))?",
            value,
        )
        return _number(number[1], language) if number else None

    if stage in ("check_in", "check_out"):
        today = today if today is not None else timezone.localdate()
        anchor = check_in if stage == "check_out" else None
        config = _date_config(language)
        try:
            return _parse_date(value, language, anchor, today, config=config) or _extract_dates(
                stage, value, language, anchor, today, config,
            )
        except (OverflowError, ValueError):
            return None
    return None


def _date_config(language):
    defaults = {
        "numeric_date_order": "mdy" if language == "en" else "dmy",
        "allow_iso_dates": True,
        "allow_slash_dates": True,
        "allow_dot_dates": True,
        "allow_dash_dates": True,
        "allow_yearless_dates": True,
        "allow_two_digit_years": True,
    }
    from .models import ParserConfiguration

    config = ParserConfiguration.objects.filter(language=language).first()
    if config is None:
        return defaults
    return {key: getattr(config, key) for key in defaults}


def _extract_dates(stage, value, language, check_in, today, config):
    if AMBIGUITY_MARKERS[language].search(value):
        return None
    matches = list(DATE_FRAGMENTS[language].finditer(value))
    if not matches:
        if stage == "check_out" and check_in:
            durations = _duration_fragments(value, language)
            if len(durations) == 1:
                return _parse_date(durations[0], language, check_in, today, config=config)
        return None
    first, *others = matches
    if others:
        if stage != "check_in" or len(others) != 1:
            return None
        second = others[0]
        gap = value[first.end():second.start()].strip(" ,")
        direct_link = bool(re.fullmatch(RANGE_LINK[language], gap))
        contextual_pair = (
            DATE_CONTEXT["check_in"][language].search(value[:first.start()])
            and DATE_CONTEXT["check_out"][language].search(gap)
        )
        if not (direct_link or contextual_pair):
            return None
        arrival = _parse_date(first[0], language, None, today, config=config)
        if arrival is None:
            return None
        departure = _parse_date(second[0], language, None, today, calendar_anchor=arrival, config=config)
        if departure is None:
            return None
        if _duration_fragments(value[second.end():], language):
            return arrival
        return _stay_dates(arrival, departure, today)

    if stage == "check_in":
        durations = _duration_fragments(value[first.end():], language)
        if durations:
            arrival = _parse_date(first[0], language, None, today, config=config)
            if arrival is not None and len(durations) == 1:
                departure = _parse_date(durations[0], language, arrival, today, config=config)
                return _stay_dates(arrival, departure, today)
            return arrival
    remainder = f"{value[:first.start()]} {value[first.end():]}"
    if DATE_CONTEXT[stage][language].search(remainder) or not DATE_FILLERS[language].sub("", remainder).strip(" ,.!?"):
        return _parse_date(first[0], language, None, today, config=config)
    return None


def _duration_fragments(value, language):
    prefix = re.compile(rf"^(?:{DURATION_START[language]}) ")
    return [
        match[0] for match in DURATION_FRAGMENTS[language].finditer(value)
        if prefix.match(match[0]) or (match[1] and _number(match[1], language) is not None)
    ]


def _stay_dates(arrival, departure, today):
    if departure and arrival >= today and departure > arrival:
        return StayDates(arrival, departure)
    return arrival


def _parse_date(value, language, check_in, today, *, calendar_anchor=None, config=None):
    config = config or _date_config(language)
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
    if parsed := _numeric_date(value, language, today, config, calendar_anchor):
        return parsed
    for fmt in DATE_FORMATS[language]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return _calendar_date(value, language, calendar_anchor or today)


def _numeric_date(value, language, today, config, calendar_anchor):
    match = re.fullmatch(r"([0-9]{4}|[0-9]{1,2})([./-])([0-9]{1,2})(?:\2([0-9]{1,4}))?", value)
    if not match:
        return None
    first, separator, second, trailing = match.groups()
    allowed_separator = {
        "/": config["allow_slash_dates"],
        ".": config["allow_dot_dates"],
        "-": config["allow_dash_dates"],
    }[separator]
    if len(first) == 4:
        if not trailing or len(trailing) > 2:
            return None
        if not config["allow_iso_dates"] or (separator != "-" and not allowed_separator):
            return None
        year, month, day = int(first), int(second), int(trailing)
    else:
        year_text = trailing
        if not allowed_separator:
            return None
        if not year_text and not config["allow_yearless_dates"]:
            return None
        if year_text:
            if len(year_text) not in (2, 4):
                return None
            if len(year_text) == 2 and not config["allow_two_digit_years"]:
                return None
        day, month = (int(first), int(second)) if config["numeric_date_order"] == "dmy" else (int(second), int(first))
        if year_text:
            year = int(year_text)
            if len(year_text) == 2:
                year += 2000 if year <= 68 else 1900
        else:
            anchor = calendar_anchor or today
            year = anchor.year + (month < anchor.month)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_duration(check_in, amount, unit):
    if unit in MONTH_UNITS:
        year, month_index = divmod(check_in.month - 1 + amount, 12)
        year += check_in.year
        month = month_index + 1
        return date(year, month, min(check_in.day, monthrange(year, month)[1]))
    return check_in + timedelta(days=amount * (7 if unit in WEEK_UNITS else 1))


def _calendar_date(value, language, anchor):
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
