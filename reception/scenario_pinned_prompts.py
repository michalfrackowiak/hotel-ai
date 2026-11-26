"""Load effective prompts from an immutable browser scenario version."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .scenario import DELIVERY_RETRY_PROMPT, _specs
from .scenario_pinning import pinned_scenario_version_id, published_pinned_steps, web_scenario_channel
from .scenario_step_settings import (
    CHAT_FIELD, OUTCOME_PROMPTS, STEP_PROMPTS, VOICE_FIELD, prompt_bindings, validate_step_configuration,
)


@dataclass(frozen=True)
class _PinnedPrompt:
    chat_text: str = ""
    voice_parts: tuple[dict, ...] = ()


def pinned_overrides(state):
    """Return legacy lookup control or an immutable version's prompt mapping.

    None means the conversation has no concrete version and can use the
    existing prompt lookup. An empty mapping deliberately uses built-in
    wording for a pinned reply with unavailable or unsupported overrides.
    """
    version_id = pinned_scenario_version_id(state)
    if version_id is None:
        return None

    language = state["language"]
    channel = web_scenario_channel(state)
    bindings = browser_prompt_bindings(state, channel)
    if not bindings:
        return {}
    steps = (
        published_pinned_steps(state).filter(key__in=bindings)
        .values_list("key", "configuration")[:len(bindings)]
    )
    return browser_prompt_overrides(language, channel, bindings, steps)


def browser_prompt_bindings(state, channel):
    """Select prompt bindings relevant to the current reply."""
    language = state["language"]
    specs = set(_specs(state))
    if (language, "rooms") in specs:
        specs.add((language, "option"))
    bindings = {}
    for step in STEP_PROMPTS.keys() | OUTCOME_PROMPTS.keys():
        relevant = {
            prompt: fields for prompt, fields in prompt_bindings(language, channel, step).items()
            if (language, prompt) in specs
        }
        if relevant:
            bindings[step] = relevant
    return bindings


def browser_prompt_overrides(language, channel, bindings, steps):
    """Render the same configuration mapping for live and preview graphs."""
    overrides = {}
    for step, configuration in steps:
        try:
            validate_step_configuration(language, channel, step, configuration)
        except ValidationError:
            continue
        for prompt, fields in bindings[step].items():
            chat_text = configuration.get(fields.get(CHAT_FIELD), "")
            voice_parts = configuration.get(fields.get(VOICE_FIELD), ())
            if prompt == DELIVERY_RETRY_PROMPT:
                chat_text = chat_text or configuration.get(CHAT_FIELD, "")
                voice_parts = voice_parts or configuration.get(VOICE_FIELD, ())
            if chat_text or voice_parts:
                overrides[language, prompt] = _PinnedPrompt(chat_text, tuple(voice_parts))
    return overrides
