"""Load effective prompts from an immutable browser scenario version."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef

from .models import ScenarioPublication, ScenarioStep
from .scenario import _specs
from .scenario_pinning import pinned_scenario_version_id, web_scenario_channel
from .scenario_step_settings import CHAT_FIELD, STEP_PROMPTS, VOICE_FIELD, prompt_key, validate_step_configuration


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
    specs = set(_specs(state))
    keys_by_step = {
        step: (language, key)
        for step in STEP_PROMPTS
        if (key := prompt_key(language, step)) and (language, key) in specs
    }
    if not keys_by_step:
        return {}

    publication = ScenarioPublication.objects.filter(
        scenario_id=OuterRef("version__scenario_id"),
        selected_version_id=OuterRef("version_id"),
        action="publish",
    )
    steps = (
        ScenarioStep.objects.filter(
            version_id=version_id,
            version__published_at__isnull=False,
            version__scenario__language=language,
            version__scenario__channel=channel,
            key__in=keys_by_step,
        )
        .filter(Exists(publication))
        .values_list("key", "configuration")[:len(keys_by_step)]
    )
    overrides = {}
    for step, configuration in steps:
        try:
            validate_step_configuration(language, channel, step, configuration)
        except ValidationError:
            continue
        if configuration:
            overrides[keys_by_step[step]] = _PinnedPrompt(
                chat_text=configuration.get(CHAT_FIELD, ""),
                voice_parts=tuple(configuration.get(VOICE_FIELD, ())),
            )
    return overrides
