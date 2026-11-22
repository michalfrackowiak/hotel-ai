"""Internal version pin for a browser booking conversation."""

from django.db.models import Exists, F, OuterRef, Subquery

from .models import Scenario, ScenarioPublication, ScenarioStep


_PIN = "scenario_pin"
_WEB_CHANNELS = ("chat", "voice")
_LANGUAGES = ("en", "pl")


def _selected_version_id(language, channel):
    history = ScenarioPublication.objects.filter(scenario_id=OuterRef("pk"))
    latest_selection = history.order_by("-pk").values("selected_version_id")[:1]
    original_publication = history.filter(action="publish", selected_version_id=OuterRef("published_version_id"))
    return (
        Scenario.objects.filter(
            language=language,
            channel=channel,
            published_version__scenario_id=F("pk"),
            published_version__published_at__isnull=False,
            published_version_id=Subquery(latest_selection),
        )
        .filter(Exists(original_publication))
        .values_list("published_version_id", flat=True)
        .first()
    )


def pin_web_scenario(state, *, channel):
    """Capture the selected version, including a deliberate absent selection."""
    if _PIN in state:
        return state
    language = state["language"]
    if language not in _LANGUAGES or channel not in _WEB_CHANNELS:
        raise ValueError("A web scenario needs a supported language and channel.")
    state[_PIN] = {
        "language": language,
        "channel": channel,
        "version_id": _selected_version_id(language, channel),
    }
    return state


def _active_pin(state):
    pin = state.get(_PIN)
    if (
        isinstance(pin, dict)
        and pin.get("language") in _LANGUAGES
        and pin["language"] == state.get("language")
        and pin.get("channel") in _WEB_CHANNELS
    ):
        return pin
    return {}


def web_scenario_channel(state):
    """Return the original channel of a pinned browser conversation."""
    return _active_pin(state).get("channel")


def pinned_scenario_version_id(state):
    """Return the recorded version for the current language without reselecting."""
    version_id = _active_pin(state).get("version_id")
    return version_id if type(version_id) is int and version_id > 0 else None


def published_pinned_steps(state):
    """Select steps from the scoped version originally published for this pin."""
    publication = ScenarioPublication.objects.filter(
        scenario_id=OuterRef("version__scenario_id"),
        selected_version_id=OuterRef("version_id"),
        action="publish",
    )
    return ScenarioStep.objects.filter(
        version_id=pinned_scenario_version_id(state),
        version__published_at__isnull=False,
        version__scenario__language=state["language"],
        version__scenario__channel=web_scenario_channel(state),
    ).filter(Exists(publication))


def carry_web_scenario(previous, state):
    """Keep the original channel through state replacement and language selection."""
    if _PIN not in previous:
        return state
    channel = web_scenario_channel(previous)
    if channel and previous["language"] != state["language"]:
        state.pop(_PIN, None)
        return pin_web_scenario(state, channel=channel)

    pin = previous[_PIN]
    state[_PIN] = pin.copy() if isinstance(pin, dict) else pin
    return state
