"""Render staff-edited booking prompts without changing conversation state."""

from decimal import Decimal
from textwrap import wrap

from .contacts import speak_contact
from .speech import MAX_TEXT_CHARACTERS


ENGLISH_WEB_GREETING = "Hi, this is Neptun Resort Jastrzębia Góra."
WELCOME_KEYS = (("pl", "welcome"), ("en", "welcome_english_option"), ("pl", "welcome_polish_option"))
WELCOME_INDEX = {pair: index for index, pair in enumerate(WELCOME_KEYS)}
PHONE_COPY = {
    "en": {
        "handover_unavailable": "Reception is unavailable by phone right now. Please try again later. Goodbye.",
        "silence": "I didn't hear an answer. Please try again.",
        "ended": "This call has ended. Please call again to make a booking. Goodbye.",
    },
    "pl": {
        "handover_unavailable": "Recepcja jest teraz niedostępna przez telefon. Spróbuj ponownie później. Do widzenia.",
        "silence": "Nie usłyszałem odpowiedzi. Spróbuj ponownie.",
        "ended": "Połączenie zostało zakończone. Zadzwoń ponownie, aby dokonać rezerwacji. Do widzenia.",
    },
}
RECEIPT_KEYS = (
    "receipt_waiting", "receipt_pending", "receipt_unconfirmed", "receipt_sent_email", "receipt_sent_sms",
    "receipt_reference",
)


def scenario_keys(language):
    from .conversation import COPY

    # The combined-date retry is configured on the versioned arrival step.
    excluded = {"welcome", "voice_option", "and", "stay_dates_required"}
    booking = tuple(key for key in COPY[language] if key not in excluded)
    welcome = tuple(key for locale, key in WELCOME_KEYS if locale == language)
    web = ("web_check_in",) if language == "en" else ()
    return (*welcome, *web, *booking, *RECEIPT_KEYS, *(f"phone_{key}" for key in PHONE_COPY[language]))


def default_chat_text(language, key):
    from .conversation import COPY, WELCOME_PARTS
    from .receipt import COPY as RECEIPT_COPY, SENT

    if (language, key) in WELCOME_INDEX:
        return WELCOME_PARTS[WELCOME_INDEX[language, key]][1]
    if key == "web_check_in":
        return f"{ENGLISH_WEB_GREETING} {COPY[language]['check_in']}"
    if key.startswith("receipt_sent_"):
        return SENT[language][key.removeprefix("receipt_sent_")]
    if key.startswith("receipt_"):
        return RECEIPT_COPY[language][key.removeprefix("receipt_")]
    if key.startswith("phone_"):
        return PHONE_COPY[language][key.removeprefix("phone_")]
    return COPY[language][key]


def default_voice_parts(language, key):
    from .conversation import ARRIVAL_PARTS, COPY

    if key in ("check_in", "web_check_in"):
        return [dict(part) for part in ARRIVAL_PARTS[language]]
    text = COPY[language]["voice_option"] if key == "option" else default_chat_text(language, key)
    return [{"text": text, "seconds": 0, "listen": False}]


def set_prompt(state, key, values=None, *, tail=None, reply=None):
    """Bind a renderable prompt to a state, retaining its existing literal fallback."""
    values = {name: str(value) for name, value in (values or {}).items()}
    state.update(prompt_key=key, prompt_values=values)
    if tail:
        state["prompt_tail"] = tail
    else:
        state.pop("prompt_tail", None)
    if reply is None:
        specs = _specs(state)
        reply = " ".join(default_chat_text(language, name).format_map(values) for language, name in specs)
    state["reply"] = reply
    return state


def _specs(state):
    key = _prompt_key(state)
    if not key:
        return ()
    if key == "welcome":
        return WELCOME_KEYS
    language = state["language"]
    tail = state.get("prompt_tail")
    return ((language, key), (language, tail)) if tail else ((language, key),)


def _prompt_key(state):
    if key := state.get("prompt_key"):
        return key
    stage = state.get("stage")
    language = state["language"]
    if stage == "welcome":
        from .conversation import WELCOME

        if state.get("reply") == WELCOME:
            return "welcome"
    if stage == "check_in":
        if state.get("reply") == default_chat_text(language, "check_in"):
            return "check_in"
        if language == "en" and state.get("reply") == default_chat_text(language, "web_check_in"):
            return "web_check_in"
    return None


def scenario_overrides(state):
    from .models import ScenarioPrompt

    specs = _specs(state)
    if not specs:
        return {}
    if specs[0][1] == "rooms":
        specs = (*specs, (state["language"], "option"))
    return {
        (row.language, row.key): row
        for row in ScenarioPrompt.objects.filter(
            language__in={language for language, _ in specs},
            key__in={key for _, key in specs},
        )
    }


def room_options(state, *, voice, overrides=None):
    from .conversation import COPY
    from .legacy_conversation import _price

    language = state["language"]
    option = (overrides or {}).get((language, "option"))
    if voice:
        template = _voice_parts(language, "option", overrides or {})[0]["text"]
    else:
        template = (option.chat_text if option else "") or COPY[language]["option"]
    items = [
        template.format(
            number=number, room=choice["room"], total=_price(Decimal(choice["total_price"]), language),
        )
        for number, choice in enumerate(state["choices"], 1)
    ]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])}, {COPY[language]['and']} {items[-1]}"


def _values(state, overrides, *, voice):
    values = state.get("prompt_values", {})
    if voice and state.get("stage") == "contact_confirm":
        return {**values, "contact": speak_contact(state["contact_candidate"], state["language"])}
    if state.get("prompt_key") != "rooms":
        return values
    return {**values, "options": room_options(state, voice=voice, overrides=overrides)}


def _chat_text(language, key, overrides):
    row = overrides.get((language, key))
    return (row.chat_text if row else "") or default_chat_text(language, key)


def _voice_parts(language, key, overrides):
    row = overrides.get((language, key))
    return (row.voice_parts if row else []) or default_voice_parts(language, key)


def render_chat(state, *, overrides=None):
    specs = _specs(state)
    if not specs:
        return state["reply"]
    overrides = scenario_overrides(state) if overrides is None else overrides
    if not any(row.chat_text for row in overrides.values()):
        return state["reply"]
    try:
        values = _values(state, overrides, voice=False)
        return " ".join(
            _chat_text(language, key, overrides).format_map(values)
            for language, key in specs
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return state["reply"]


def voice_segments(state, *, overrides=None):
    specs = _specs(state)
    if not specs:
        return []
    overrides = scenario_overrides(state) if overrides is None else overrides
    edited = any(row.voice_parts for row in overrides.values())
    contact = state.get("stage") == "contact_confirm"
    if not (edited or contact) and specs[0][1] not in ("check_in", "web_check_in"):
        return []
    try:
        values = _values(state, overrides, voice=True)
        parts = [
            {**part, "language": language, "text": part["text"].format_map(values)}
            for language, key in specs
            for part in _voice_parts(language, key, overrides)
        ]
        if contact:
            return split_voice_parts(parts)
        return parts
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return []


def split_voice_parts(parts):
    result = []
    for part in parts:
        chunks = wrap(part["text"], width=MAX_TEXT_CHARACTERS) or [part["text"]]
        result.extend({**part, "text": chunk, "seconds": 0, "listen": False} for chunk in chunks[:-1])
        result.append({**part, "text": chunks[-1]})
    return result
