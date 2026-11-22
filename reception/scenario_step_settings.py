"""Validated prompt overrides stored on a versioned draft step."""

from django.core.exceptions import ValidationError

from .scenario_answers import answer_label_fields, validate_answer_labels
from .scenario_editor import validate_prompt


CHAT_FIELD = "chat_text"
VOICE_FIELD = "voice_parts"
ARRIVAL_INPUT_FIELD = "arrival_input"
RETRY_LIMIT_FIELD = "retry_limit"
RETRY_LIMIT_STEPS = frozenset({
    "check_in", "check_out", "guests", "room", "confirm", "delivery", "email", "phone", "contact_confirm",
})
RETRY_EXHAUSTED_EVENT = "retry_exhausted"
MAX_RETRY_LIMIT = 10
REQUIRE_ARRIVAL_RANGE = "require_range"
ARRIVAL_INPUT_CHOICES = (
    ("arrival_or_range", "Arrival date or both stay dates"),
    (REQUIRE_ARRIVAL_RANGE, "Require arrival and departure together"),
)
MAX_CHAT_TEXT = 2000
CHANNEL_FIELDS = {
    "chat": (CHAT_FIELD,),
    "voice": (VOICE_FIELD,),
    "shared": (CHAT_FIELD, VOICE_FIELD),
}
STEP_PROMPTS = {
    "check_in": "check_in",
    "check_out": "check_out",
    "guests": "guests",
    "room": "rooms",
    "confirm": "confirm",
    "delivery": "delivery",
    "email": "email",
    "phone": "phone",
    "cancelled": "cancelled",
    "handover": "handover",
}
OUTCOME_PROMPTS = {
    "check_in": ("unavailable", "offer_changed", "stay_dates_required"),
    "check_out": ("check_out_retry",),
    "guests": ("stay_dates", "guests_retry"),
    "room": ("option",),
    "email": ("invalid_email",),
    "phone": ("invalid_phone",),
    "contact_confirm": ("confirm_email", "confirm_phone"),
    "delivery_pending": ("receipt_waiting",),
    "completed": (
        "receipt_pending", "receipt_unconfirmed", "receipt_sent_email", "receipt_sent_sms", "receipt_reference",
    ),
}


def outcome_field(prompt, kind):
    return f"{prompt}_{kind}"


def prompt_key(language, step_key):
    if language == "en" and step_key == "check_in":
        return "web_check_in"
    return STEP_PROMPTS.get(step_key)


def prompt_fields(language, channel, step_key):
    kinds = CHANNEL_FIELDS.get(channel, ())
    primary = kinds if step_key in STEP_PROMPTS else ()
    outcomes = (outcome_field(prompt, kind) for prompt in OUTCOME_PROMPTS.get(step_key, ()) for kind in kinds)
    parser = (ARRIVAL_INPUT_FIELD,) if step_key == "check_in" and channel in CHANNEL_FIELDS else ()
    retry = (RETRY_LIMIT_FIELD,) if step_key in RETRY_LIMIT_STEPS and channel in CHANNEL_FIELDS else ()
    return (*primary, *outcomes, *answer_label_fields(language, channel, step_key), *parser, *retry)


def prompt_bindings(language, channel, step_key):
    kinds = CHANNEL_FIELDS.get(channel, ())
    bindings = {
        prompt: {kind: outcome_field(prompt, kind) for kind in kinds}
        for prompt in OUTCOME_PROMPTS.get(step_key, ())
    }
    if primary := prompt_key(language, step_key):
        bindings = {primary: {kind: kind for kind in kinds}, **bindings}
    return bindings


def validate_step_configuration(language, channel, key, configuration):
    """Validate channel-specific settings without changing or using a draft."""
    if not isinstance(configuration, dict):
        raise ValidationError("Step configuration must be an object.")
    if not configuration:
        return
    if language not in {"en", "pl"} or set(configuration).difference(prompt_fields(language, channel, key)):
        raise ValidationError("This step or channel does not support the selected prompt settings.")

    validate_answer_labels(language, channel, key, configuration)
    if RETRY_LIMIT_FIELD in configuration and retry_limit_value(configuration) is None:
        raise ValidationError("Enter a whole number from 0 to %(maximum)s for the retry limit.", params={
            "maximum": MAX_RETRY_LIMIT,
        })
    if ARRIVAL_INPUT_FIELD in configuration:
        mode = configuration[ARRIVAL_INPUT_FIELD]
        if not isinstance(mode, str) or mode not in dict(ARRIVAL_INPUT_CHOICES):
            raise ValidationError("Choose a supported arrival input mode.")
    for prompt, fields in prompt_bindings(language, channel, key).items():
        chat_field, voice_field = fields.get(CHAT_FIELD), fields.get(VOICE_FIELD)
        if chat_field not in configuration and voice_field not in configuration:
            continue
        chat_text = configuration.get(chat_field, "")
        if not isinstance(chat_text, str) or len(chat_text) > MAX_CHAT_TEXT:
            raise ValidationError("Enter written wording of at most %(limit)s characters.", params={"limit": MAX_CHAT_TEXT})
        if chat_field in configuration and not chat_text.strip():
            raise ValidationError("Enter written wording or remove the override.")
        voice_parts = configuration.get(voice_field, [])
        if voice_field in configuration and voice_parts == []:
            raise ValidationError("Enter a voice segment or remove the override.")
        if errors := validate_prompt(language, prompt, chat_text, voice_parts):
            raise ValidationError([f"{prompt}: {error}" for error in errors.values()])


def retry_limit_value(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(RETRY_LIMIT_FIELD)
    return value if type(value) is int and 0 <= value <= MAX_RETRY_LIMIT else None
