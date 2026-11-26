"""Validated prompt overrides stored on a versioned draft step."""

from django.core.exceptions import ValidationError

from .scenario import DELIVERY_RETRY_PROMPT
from .scenario_answers import answer_label_fields, validate_answer_labels
from .scenario_editor import validate_prompt


CHAT_FIELD = "chat_text"
VOICE_FIELD = "voice_parts"
ARRIVAL_INPUT_FIELD = "arrival_input"
PARSER_FALLBACK_FIELD = "parser_fallback"
STAY_PARSER_STEPS = frozenset({"check_in", "check_out", "guests"})
BUILT_IN_PARSER_ONLY = "built_in_only"
PARSER_FALLBACK_CHOICES = (
    ("allow_configured", "Allow the configured model fallback"),
    (BUILT_IN_PARSER_ONLY, "Use the built-in parser only"),
)
PARSER_FALLBACK_LABELS = dict(PARSER_FALLBACK_CHOICES)
PARSER_CORRECTIONS_FIELD = "parser_corrections"
PARSER_CORRECTIONS_STEPS = STAY_PARSER_STEPS | {"room", "email", "phone"}
SKIP_PARSER_CORRECTIONS = "skip"
PARSER_CORRECTIONS_CHOICES = (
    ("apply", "Apply reviewed corrections when enabled"),
    (SKIP_PARSER_CORRECTIONS, "Skip reviewed corrections"),
)
PARSER_CORRECTIONS_LABELS = dict(PARSER_CORRECTIONS_CHOICES)
RETRY_LIMIT_FIELD = "retry_limit"
SILENCE_WINDOW_FIELD = "voice_silence_seconds"
SILENCE_WINDOW_STEPS = frozenset({
    "welcome", "check_in", "check_out", "guests", "room", "confirm", "delivery", "email", "phone", "contact_confirm",
})
MAX_SILENCE_SECONDS = 20
VOICE_INTERRUPTION_FIELD = "voice_interruption"
VOICE_INTERRUPTION_STEPS = frozenset({
    "welcome", "check_in", "check_out", "guests", "room", "delivery", "email", "phone",
})
ALLOW_VOICE_SKIP = "allow_skip"
VOICE_INTERRUPTION_CHOICES = (
    ("wait", "Play the prompt as usual"),
    (ALLOW_VOICE_SKIP, "Allow guests to skip the spoken prompt"),
)
VOICE_INTERRUPTION_LABELS = dict(VOICE_INTERRUPTION_CHOICES)
VOICE_TIMEOUT_FIELD = "voice_timeout"
VOICE_TIMEOUT_STEPS = frozenset({"welcome", "check_in"})
CONTINUE_IN_CHAT = "continue_in_chat"
VOICE_TIMEOUT_CHOICES = (
    ("default", "Follow the usual timeout route"),
    (CONTINUE_IN_CHAT, "End voice and continue in chat"),
)
VOICE_TIMEOUT_LABELS = dict(VOICE_TIMEOUT_CHOICES)
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
    "delivery": (DELIVERY_RETRY_PROMPT,),
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
    fallback = (PARSER_FALLBACK_FIELD,) if step_key in STAY_PARSER_STEPS and kinds else ()
    corrections = (PARSER_CORRECTIONS_FIELD,) if step_key in PARSER_CORRECTIONS_STEPS and kinds else ()
    retry = (RETRY_LIMIT_FIELD,) if step_key in RETRY_LIMIT_STEPS and channel in CHANNEL_FIELDS else ()
    silence = (SILENCE_WINDOW_FIELD,) if (
        step_key in SILENCE_WINDOW_STEPS and kinds and (step_key != "welcome" or language == "pl")
    ) else ()
    interruption = (VOICE_INTERRUPTION_FIELD,) if (
        step_key in VOICE_INTERRUPTION_STEPS and kinds and (step_key != "welcome" or language == "pl")
    ) else ()
    timeout = (VOICE_TIMEOUT_FIELD,) if (
        step_key in VOICE_TIMEOUT_STEPS and kinds and (step_key != "welcome" or language == "pl")
    ) else ()
    return (
        *primary, *outcomes, *answer_label_fields(language, channel, step_key),
        *parser, *fallback, *corrections, *retry, *silence, *interruption, *timeout,
    )


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
    if SILENCE_WINDOW_FIELD in configuration and silence_window_seconds(configuration) is None:
        raise ValidationError("Enter a whole number from 1 to %(maximum)s for the voice silence window.", params={
            "maximum": MAX_SILENCE_SECONDS,
        })
    if VOICE_INTERRUPTION_FIELD in configuration and voice_interruption_mode(configuration) is None:
        raise ValidationError("Choose a supported voice interruption policy.")
    if VOICE_TIMEOUT_FIELD in configuration and voice_timeout_mode(configuration) is None:
        raise ValidationError("Choose a supported voice timeout action.")
    if PARSER_FALLBACK_FIELD in configuration and parser_fallback_mode(configuration) is None:
        raise ValidationError("Choose a supported parser fallback policy.")
    if PARSER_CORRECTIONS_FIELD in configuration and parser_corrections_mode(configuration) is None:
        raise ValidationError("Choose a supported parser corrections policy.")
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
        validation_prompt = "delivery" if prompt == DELIVERY_RETRY_PROMPT else prompt
        if errors := validate_prompt(language, validation_prompt, chat_text, voice_parts):
            raise ValidationError([f"{prompt}: {error}" for error in errors.values()])


def retry_limit_value(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(RETRY_LIMIT_FIELD)
    return value if type(value) is int and 0 <= value <= MAX_RETRY_LIMIT else None


def silence_window_seconds(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(SILENCE_WINDOW_FIELD)
    return value if type(value) is int and 1 <= value <= MAX_SILENCE_SECONDS else None


def voice_interruption_mode(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(VOICE_INTERRUPTION_FIELD)
    return value if isinstance(value, str) and value in VOICE_INTERRUPTION_LABELS else None


def parser_fallback_mode(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(PARSER_FALLBACK_FIELD)
    return value if isinstance(value, str) and value in PARSER_FALLBACK_LABELS else None


def allows_model_fallback(configuration):
    return parser_fallback_mode(configuration) != BUILT_IN_PARSER_ONLY


def parser_corrections_mode(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(PARSER_CORRECTIONS_FIELD)
    return value if isinstance(value, str) and value in PARSER_CORRECTIONS_LABELS else None


def allows_parser_corrections(configuration):
    return parser_corrections_mode(configuration) != SKIP_PARSER_CORRECTIONS


def voice_timeout_mode(configuration):
    if not isinstance(configuration, dict):
        return None
    value = configuration.get(VOICE_TIMEOUT_FIELD)
    return value if isinstance(value, str) and value in VOICE_TIMEOUT_LABELS else None
