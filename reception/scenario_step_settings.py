"""Validated prompt overrides stored on a versioned draft step."""

from django.core.exceptions import ValidationError

from .scenario_editor import validate_prompt


CHAT_FIELD = "chat_text"
VOICE_FIELD = "voice_parts"
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


def prompt_key(language, step_key):
    if language == "en" and step_key == "check_in":
        return "web_check_in"
    return STEP_PROMPTS.get(step_key)


def prompt_fields(channel, step_key):
    if step_key in STEP_PROMPTS:
        return CHANNEL_FIELDS.get(channel, ())
    return ()


def validate_step_configuration(language, channel, key, configuration):
    """Validate channel-specific settings without changing or using a draft."""
    if not isinstance(configuration, dict):
        raise ValidationError("Step configuration must be an object.")
    if not configuration:
        return
    if language not in {"en", "pl"} or set(configuration).difference(prompt_fields(channel, key)):
        raise ValidationError("This step or channel does not support the selected prompt settings.")

    chat_text = configuration.get(CHAT_FIELD, "")
    if not isinstance(chat_text, str) or len(chat_text) > MAX_CHAT_TEXT:
        raise ValidationError("Enter written wording of at most %(limit)s characters.", params={"limit": MAX_CHAT_TEXT})
    if CHAT_FIELD in configuration and not chat_text.strip():
        raise ValidationError("Enter written wording or remove the override.")
    voice_parts = configuration.get(VOICE_FIELD, [])
    if VOICE_FIELD in configuration and voice_parts == []:
        raise ValidationError("Enter a voice segment or remove the override.")
    if errors := validate_prompt(language, prompt_key(language, key), chat_text, voice_parts):
        raise ValidationError(list(errors.values()))
