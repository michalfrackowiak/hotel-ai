"""Canonical suggestions and their optional presentation settings."""

from django.core.exceptions import ValidationError

from .scenario_editor import INVALID_XML_CHARACTERS, _template_error


MAX_ANSWER_LABEL = 80
FIXED_ANSWERS = {
    "check_in": {
        "en": ("Today", "Tomorrow", "Day after tomorrow"),
        "pl": ("Dzisiaj", "Jutro", "Pojutrze"),
    },
    "check_out": {
        "en": ("1 night", "2 nights", "3 nights"),
        "pl": ("1 noc", "2 noce", "3 noce"),
    },
    "guests": {language: ("1", "2", "3", "4") for language in ("en", "pl")},
}
ROOM_ANSWER_LABEL = "room_answer_label"


def answer_label_defaults(language, step):
    if step == "room":
        return {ROOM_ANSWER_LABEL: "{room}"}
    return {
        f"answer_label_{number}": answer
        for number, answer in enumerate(FIXED_ANSWERS.get(step, {}).get(language, ()), 1)
    }


def answer_label_fields(channel, step):
    return tuple(answer_label_defaults("en", step)) if channel in ("chat", "shared") else ()


def validate_answer_labels(channel, step, configuration):
    for field in answer_label_fields(channel, step):
        if field not in configuration:
            continue
        value = configuration[field]
        if (
            not isinstance(value, str) or not value.strip() or len(value) > MAX_ANSWER_LABEL
            or value.splitlines() != [value] or INVALID_XML_CHARACTERS.search(value)
        ):
            raise ValidationError(f"Enter a single-line button label of at most {MAX_ANSWER_LABEL} characters.")
        if field == ROOM_ANSWER_LABEL and (error := _template_error([value], {"room"})):
            raise ValidationError(error)


def suggestions(state, labels):
    stage = state["stage"]
    if stage == "room":
        template = labels.get(ROOM_ANSWER_LABEL, "{room}")
        return [
            {"label": template.format(room=offer["room"]), "text": str(number)}
            for number, offer in enumerate(state["choices"], 1)
        ]
    return [
        {"label": labels.get(field, answer), "text": answer}
        for field, answer in answer_label_defaults(state["language"], stage).items()
    ]
