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
ENGLISH_CHOICE = {"answer_label_choose_english": "English"}
QUICK_REPLIES = {
    "confirm": {
        "en": {"answer_label_approve_quote": "Yes, confirm", "answer_label_decline_quote": "No"},
        "pl": {"answer_label_approve_quote": "Tak, potwierdzam", "answer_label_decline_quote": "Nie"},
    },
    "delivery": {
        language: {"answer_label_email": "E-mail", "answer_label_sms": "SMS"} for language in ("en", "pl")
    },
    "contact_confirm": {
        "en": {
            "answer_label_approve_contact": "Yes, this is correct",
            "answer_label_edit_contact": "Change contact details",
            "answer_label_change_delivery": "Change delivery method",
        },
        "pl": {
            "answer_label_approve_contact": "Tak, dane są poprawne",
            "answer_label_edit_contact": "Popraw dane kontaktowe",
            "answer_label_change_delivery": "Zmień sposób dostawy",
        },
    },
}


def quick_reply_defaults(language, step):
    if language == "pl" and step in ("welcome", "check_in"):
        return ENGLISH_CHOICE
    return QUICK_REPLIES.get(step, {}).get(language, {})


def _fixed_suggestions(language, step):
    return {
        f"answer_label_{number}": answer
        for number, answer in enumerate(FIXED_ANSWERS.get(step, {}).get(language, ()), 1)
    }


def answer_label_defaults(language, step):
    if step == "room":
        return {ROOM_ANSWER_LABEL: "{room}"}
    return {**_fixed_suggestions(language, step), **quick_reply_defaults(language, step)}


def answer_label_fields(language, channel, step):
    return tuple(answer_label_defaults(language, step)) if channel in ("chat", "shared") else ()


def validate_answer_labels(language, channel, step, configuration):
    for field in answer_label_fields(language, channel, step):
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
        for field, answer in _fixed_suggestions(state["language"], stage).items()
    ]
