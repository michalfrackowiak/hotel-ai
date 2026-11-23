import json
import re
from datetime import date
from hashlib import sha256
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_safe

from model_providers.extraction import extract_stay_value

from .booking_draft import DATE_STAGES, date_picker, edit_actions, parse_selection, rewind, selected_dates, summary
from .contacts import parse_email
from .conversation import COPY as BOOKING_COPY
from .conversation import TERMINAL_STAGES, WELCOME_PARTS, new_conversation, selects_english
from .models import Conversation, StaffHandover
from .parser_review import review_booking_input
from .parsing import parse_stay_input
from .receipt import DELIVERY_PENDING, refresh_receipt
from .scenario import (
    ENGLISH_WEB_GREETING, render_chat, scenario_overrides, set_prompt, split_voice_parts, voice_segments,
)
from .scenario_answers import answer_label_fields, quick_reply_defaults, suggestions, validate_answer_labels
from .scenario_dispatch import RETRY_HANDOVER, advance_web_turn, resolve_web_transition
from .scenario_pinned_prompts import pinned_overrides
from .scenario_pinning import (
    carry_web_scenario, pin_web_scenario, pinned_scenario_version_id, published_pinned_steps, web_scenario_channel,
)
from .scenario_step_settings import (
    ALLOW_VOICE_SKIP, ARRIVAL_INPUT_FIELD, CONTINUE_IN_CHAT, PARSER_CORRECTIONS_STEPS, REQUIRE_ARRIVAL_RANGE,
    SILENCE_WINDOW_STEPS, STAY_PARSER_STEPS, VOICE_INTERRUPTION_STEPS, VOICE_TIMEOUT_STEPS, allows_model_fallback,
    allows_parser_corrections, silence_window_seconds, voice_interruption_mode, voice_timeout_mode,
)
from .speech import (
    MAX_PCM_BYTES, MAX_TEXT_CHARACTERS, SpeechBusy, SpeechUnavailable, synthesize, synthesize_welcome, transcribe,
    transcribe_welcome,
)


MAX_BODY_BYTES = 4096
MAX_AUDIO_REQUEST_BYTES = 128
MAX_MESSAGE_LENGTH = 500
HANDOVER_PAGE_SIZE = 25
RECEPTION_TITLES = {
    "combined": {"en": ("Book a room", "Book a room"), "pl": ("Rezerwacja pokoju", "Zarezerwuj pokój")},
    "chat": {"en": ("Reception chat", "Reception chat"), "pl": ("Czat z recepcją", "Czat z recepcją")},
    "voice": {"en": ("Voice booking", "Voice booking"), "pl": ("Rozmowa głosowa", "Rozmowa głosowa")},
}
OLD_ARRIVAL_PROMPTS = (
    "Dzień dobry, Resort Neptun Jastrzębia Góra.",
    "Welcome to Resort Neptun Jastrzębia Góra.",
    "Aby sprawdzić dostępność, ceny lub dokonać rezerwacji, podaj planowaną datę przyjazdu.",
    "To check availability, prices, or make a reservation, please give your planned arrival date.",
    BOOKING_COPY["en"]["check_in"],
)


@never_cache
@ensure_csrf_cookie
@require_safe
def index(request, mode="combined"):
    language = request.GET.get("lang")
    valid_language = language in ("en", "pl")
    resume = mode != "combined" and (request.GET.get("continue") == "1" or not valid_language)
    if resume or not valid_language:
        language = language if valid_language else "pl"
        if request.session.session_key:
            state = Conversation.objects.filter(session_id=request.session.session_key).values_list("state", flat=True).first()
            if state:
                language = state["language"]
    request.session.setdefault("reception", True)
    page_title, heading = RECEPTION_TITLES[mode][language]
    return render(request, "reception/index.html", {
        "language": language,
        "mode": mode,
        "page_title": page_title,
        "heading": heading,
        "resume_conversation": resume,
        "piper_enabled": bool(settings.HOTEL_PIPER_URL or settings.HOTEL_SPEECH_ROUTE_TARGETS),
        "asr_enabled": bool(settings.HOTEL_ASR_URL or settings.HOTEL_SPEECH_ROUTE_TARGETS),
        "parser_training_enabled": settings.HOTEL_PARSER_TRAINING_ENABLED,
    })


def _result(state, *, stale=False, cursor=0, date_error=False):
    payload = {field: state[field] for field in ("language", "stage", "revision", "reply")}
    pinned = pinned_overrides(state)
    overrides = scenario_overrides(state) if pinned is None else pinned
    payload["reply"] = render_chat(state, overrides=overrides)
    if state["stage"] in ("completed", DELIVERY_PENDING):
        payload["reference"] = state["reference"]
    if state["stage"] == "contact_confirm":
        payload["contact_candidate"] = state["contact_candidate"]
        payload["can_change_delivery"] = state.get("flow") == "neptun"
    if state["stage"] == "welcome":
        payload["welcome_parts"] = WELCOME_PARTS
    if parts := _browser_voice_parts(state, overrides=overrides):
        if (
            pinned is None
            and state["stage"] != "contact_confirm"
            and not any(row.chat_text for row in overrides.values())
        ):
            payload["reply"] = " ".join(part["text"] for part in parts)
        payload["voice_parts"] = parts
        payload["voice_key"] = _voice_key(parts)
        if state["stage"] == "check_in" and any(part["listen"] for part in parts):
            payload["arrival_parts"] = parts
    elif payload["reply"] != state["reply"]:
        payload["voice_reply"] = state["reply"]
    step_configuration = _pinned_step_configuration(state, state["stage"])
    if draft := summary(state):
        payload["booking_summary"] = draft
    if state["stage"] in DATE_STAGES:
        arrival_configuration = (
            _pinned_step_configuration(state, "check_in") if state["stage"] == "welcome" else step_configuration
        )
        if picker := date_picker(state, require_range=_requires_arrival_range(arrival_configuration)):
            payload["date_picker"] = picker
    if date_error:
        payload["date_error"] = True
    if state["stage"] in SILENCE_WINDOW_STEPS and (seconds := silence_window_seconds(step_configuration)):
        payload["voice_silence_seconds"] = seconds
    if state["stage"] in VOICE_INTERRUPTION_STEPS and voice_interruption_mode(step_configuration) == ALLOW_VOICE_SKIP:
        payload["voice_can_skip_prompt"] = True
    if state["stage"] in VOICE_TIMEOUT_STEPS and voice_timeout_mode(step_configuration) == CONTINUE_IN_CHAT:
        payload["voice_timeout_continue_in_chat"] = True
    answer_labels = _pinned_answer_labels(state, step_configuration)
    if not (state["stage"] == "check_in" and _requires_arrival_range(step_configuration)) and (
        suggested := suggestions(state, answer_labels)
    ):
        payload["suggestions"] = suggested
    if quick_replies := {
        field: answer_labels[field]
        for field in quick_reply_defaults(state["language"], state["stage"])
        if field in answer_labels
    }:
        payload["quick_reply_labels"] = quick_replies
    if state["stage"] == "check_in" and state["language"] == "pl" and state.get("voice_welcome"):
        payload["voice_welcome"] = True
    if state["stage"] == "handover":
        handover = StaffHandover.objects.get(pk=state["handover_id"])
        payload["handover_closed"] = handover.closed
        payload["voice_welcome"] = (
            not state.get(RETRY_HANDOVER) and not handover.closed and not handover.messages.exists()
        )
        payload["handover_messages"] = list(
            handover.messages.filter(id__gt=cursor).order_by("id").values("id", "staff", "text")[:HANDOVER_PAGE_SIZE]
        )
    if stale:
        payload["stale"] = True
    return JsonResponse(payload, status=409 if stale else 200)


def _pinned_step_configuration(state, stage):
    if pinned_scenario_version_id(state) is None:
        return {}
    configured_stages = (
        SILENCE_WINDOW_STEPS | VOICE_INTERRUPTION_STEPS | VOICE_TIMEOUT_STEPS | PARSER_CORRECTIONS_STEPS
    )
    if stage not in configured_stages and not answer_label_fields(
        state["language"], web_scenario_channel(state), stage,
    ):
        return {}
    configuration = published_pinned_steps(state).filter(key=stage).values_list("configuration", flat=True).first()
    return configuration if isinstance(configuration, dict) else {}


def _requires_arrival_range(configuration):
    return configuration.get(ARRIVAL_INPUT_FIELD) == REQUIRE_ARRIVAL_RANGE


def _pinned_answer_labels(state, configuration):
    language, stage = state["language"], state["stage"]
    fields = answer_label_fields(language, "chat", stage)
    if not fields or web_scenario_channel(state) != "chat":
        return {}
    try:
        validate_answer_labels(language, "chat", stage, configuration)
    except ValidationError:
        return {}
    return {field: configuration[field] for field in fields if field in configuration}


def _invalid(status=400):
    return JsonResponse({"error": "invalid_message"}, status=status)


def _voice_key(parts):
    return sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _browser_voice_parts(state, *, overrides=None):
    parts = voice_segments(state, overrides=overrides)
    if not parts and state["stage"] == "welcome":
        return [
            {"language": language, "text": text, "seconds": 0, "listen": False}
            for language, text in WELCOME_PARTS
        ]
    if pinned_scenario_version_id(state) is not None:
        bounded_parts = []
        for part in parts:
            if len(part["text"]) > MAX_TEXT_CHARACTERS:
                bounded_parts.extend(split_voice_parts([part]))
            else:
                bounded_parts.append(part)
        return bounded_parts
    return parts


def _new_web_conversation(language, *, channel=None):
    if language == "pl":
        state = new_conversation(language)
    else:
        state = new_conversation("en", "check_in")
        set_prompt(state, "web_check_in", reply=f"{ENGLISH_WEB_GREETING} {state['reply']}")
    if channel is not None:
        pin_web_scenario(state, channel=channel)
    return state


def _save_state(conversation, state, *, fresh=False):
    if not fresh:
        carry_web_scenario(conversation.state, state)
    if state != conversation.state:
        conversation.state = state
        conversation.save(update_fields=["state"])


@transaction.atomic
def _poll_receipt(session_id, language, revision):
    conversation = Conversation.objects.filter(session_id=session_id).first()
    if conversation is None:
        return HttpResponse(status=404)
    state = conversation.state
    if state["stage"] != DELIVERY_PENDING or state["revision"] != revision or state["language"] != language:
        return _result(state, stale=True)
    state = refresh_receipt(state, resolve_transition=resolve_web_transition)
    _save_state(conversation, state)
    return _result(state)


@never_cache
@require_POST
def message(request):
    if request.content_type != "application/json":
        return _invalid(415)
    try:
        body = request.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return _invalid(413)
        payload = json.loads(body)
    except (UnreadablePostError, ValueError, RecursionError):
        return _invalid()
    if not isinstance(payload, dict) or payload.get("language") not in ("en", "pl"):
        return _invalid()
    language = payload["language"]
    voice = payload.get("voice") is True
    opening_keys = {"language", "voice"} if voice else {"language"}
    resume = payload.get("resume") is True
    start_keys = opening_keys | ({"start", "resume"} if resume else {"start"})
    start = payload.get("start") is True and payload.keys() == start_keys
    reset = payload.get("reset") is True and payload.keys() == opening_keys | {"reset"}
    voice_start = payload.get("voice_start") is True and payload.keys() == {"language", "voice_start", "revision"}
    poll = payload.get("poll") is True and payload.keys() == {"language", "poll", "revision", "cursor"}
    transcript_input = voice and payload.get("transcript") is True
    required_timeout_fields = {"language", "timeout", "revision"}
    timeout = payload.get("timeout") is True and (
        payload.keys() == required_timeout_fields or (voice and payload.keys() == required_timeout_fields | {"voice"})
    )
    cursor = payload.get("cursor", 0)
    text = payload.get("message")
    revision = payload.get("revision")
    required_turn_fields = {"language", "message", "revision"}
    structured_fields = opening_keys | {"revision"}
    dates = payload.keys() == structured_fields | {"dates"}
    edit = payload.keys() == structured_fields | {"edit"}
    selection = parse_selection(payload["dates"]) if dates else None
    action = payload.get("edit") if edit else None
    turn = (
        payload.keys() == required_turn_fields
        or (voice and payload.keys() == required_turn_fields | {"voice"})
        or (transcript_input and payload.keys() == required_turn_fields | {"voice", "transcript"})
    )
    if not (start or reset):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{32}", revision):
            return _invalid()
        if turn:
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_MESSAGE_LENGTH:
                return _invalid()
            text = text.strip()
        elif not (poll or timeout or voice_start or (dates and selection) or (
            edit and action in ("dates", "guests", "room")
        )):
            return _invalid()
        if poll and (type(cursor) is not int or not 0 <= cursor < 2**63):
            return _invalid()

    preview = None
    if not (start or reset) and request.session.session_key:
        preview = Conversation.objects.filter(session_id=request.session.session_key).values_list(
            "state", flat=True,
        ).first()
    if poll:
        if not preview:
            return HttpResponse(status=404)
        if preview["stage"] == DELIVERY_PENDING:
            return _poll_receipt(request.session.session_key, language, revision)
        if preview["stage"] != "handover" or preview["language"] != language:
            return _result(preview, stale=True)
        return _result(preview, stale=preview["revision"] != revision, cursor=cursor)
    parsed = None
    if (
        turn and preview and preview["revision"] == revision and preview["language"] == language
        and preview["stage"] in (STAY_PARSER_STEPS | {"welcome"})
    ):
        stage = "check_in" if preview["stage"] == "welcome" else preview["stage"]
        check_in = date.fromisoformat(preview["check_in"]) if stage == "check_out" else None
        if not (stage == "check_in" and language == "pl" and selects_english(text)):
            parsed = parse_stay_input(stage, text, language, check_in=check_in)
            if parsed is None:
                configuration = _pinned_step_configuration(preview, stage)
                if allows_model_fallback(configuration):
                    parsed = extract_stay_value(stage, text, language)

    # Recheck the revision under SQLite's write lock after parsing. Booking and
    # conversation updates commit together, including a retried confirmation.
    with transaction.atomic():
        request.session.setdefault("reception", True)
        if request.session.session_key is None:
            request.session.create()
        channel = "voice" if voice and (start or reset) else "chat"
        conversation, created = Conversation.objects.get_or_create(
            session_id=request.session.session_key,
            defaults={"state": lambda: _new_web_conversation(language, channel=channel)},
        )
        state = conversation.state
        if start and resume:
            return _result(state)
        language_changed = start and state["language"] != language
        fresh = not created and (reset or language_changed)
        if created and (start or reset):
            return _result(state)
        if fresh:
            if state["stage"] == "handover":
                StaffHandover.objects.filter(pk=state["handover_id"]).update(closed=True)
            if language_changed:
                channel = web_scenario_channel(state) or channel
            state = _new_web_conversation(language, channel=channel)
        elif start:
            old_arrival = state["stage"] == "check_in" and state["reply"].startswith(OLD_ARRIVAL_PROMPTS)
            english_welcome = language == "en" and state["stage"] == "welcome"
            if not (old_arrival or english_welcome):
                return _result(state)
            state = _new_web_conversation(language)
        elif state["revision"] != revision or state["language"] != language:
            return _result(state, stale=True)
        elif edit:
            if action not in edit_actions(state):
                return _result(state, stale=True)
            state = rewind(state, action, voice=voice)
        elif dates:
            if state["stage"] not in DATE_STAGES or state.get("flow") != "neptun":
                return _result(state, stale=True)
            require_range = _requires_arrival_range(_pinned_step_configuration(state, "check_in"))
            selected = selected_dates(state, selection, require_range=require_range)
            if selected is None:
                return _result(state, date_error=True)
            state = advance_web_turn(state, "", selected, voice=voice, require_arrival_range=require_range)
        elif voice_start:
            if language == "pl" and state["stage"] == "check_in" and state.get("voice_welcome"):
                state = _new_web_conversation(language)
            elif state["stage"] == "handover" and not state.get(RETRY_HANDOVER):
                handover = StaffHandover.objects.get(pk=state["handover_id"])
                if not handover.closed and not handover.messages.exists():
                    handover.closed = True
                    handover.save(update_fields=["closed"])
                    state = _new_web_conversation(language)
        elif state["stage"] == "handover":
            handover = StaffHandover.objects.get(pk=state["handover_id"])
            if turn:
                if handover.closed:
                    return _result(state, stale=True)
                handover.messages.create(text=text)
                state = {**state, "revision": uuid4().hex}
        elif timeout and state["stage"] not in ("welcome", "check_in"):
            return _result(state, stale=True)
        else:
            if voice and state["stage"] == "email" and state.get("flow") != "neptun":
                text = parse_email(text) or text
            was_welcome = state["stage"] == "welcome"
            input_parsed = False
            if turn and settings.HOTEL_PARSER_TRAINING_ENABLED:
                parser_stage = "check_in" if was_welcome else state["stage"]
                apply_corrections = parser_stage not in PARSER_CORRECTIONS_STEPS or allows_parser_corrections(
                    _pinned_step_configuration(state, parser_stage),
                )
                parsed, input_parsed = review_booking_input(
                    state, text, parsed, source="voice" if transcript_input else "chat",
                    apply_corrections=apply_corrections,
                )
            require_arrival_range = (
                parsed is not None and state["stage"] in ("welcome", "check_in")
                and _requires_arrival_range(_pinned_step_configuration(state, "check_in"))
            )
            state = advance_web_turn(
                state, text or "", parsed, voice=voice, input_parsed=input_parsed,
                timeout=timeout, require_arrival_range=require_arrival_range,
            )
            if was_welcome and state["stage"] == "check_in" and state["language"] == "pl" and not voice:
                state["voice_welcome"] = True
            if state["stage"] == "handover":
                handover = StaffHandover.objects.create(conversation=conversation, language=state["language"])
                if text and not state.get(RETRY_HANDOVER):
                    handover.messages.create(text=text)
                state["handover_id"] = handover.pk
        _save_state(conversation, state, fresh=fresh)
        return _result(state)


@never_cache
@require_POST
def audio(request):
    if not (settings.HOTEL_PIPER_URL or settings.HOTEL_SPEECH_ROUTE_TARGETS):
        return HttpResponse(status=503)
    if request.content_type != "application/json":
        return _invalid(415)
    try:
        body = request.read(MAX_AUDIO_REQUEST_BYTES + 1)
        if len(body) > MAX_AUDIO_REQUEST_BYTES:
            return _invalid(413)
        payload = json.loads(body)
    except (UnreadablePostError, ValueError, RecursionError):
        return _invalid()
    if not isinstance(payload, dict) or payload.keys() not in (
        {"revision"}, {"revision", "part"}, {"revision", "part", "voice_key"},
    ):
        return _invalid()
    revision = payload["revision"]
    part = payload.get("part")
    voice_key = payload.get("voice_key")
    if "part" in payload and (type(part) is not int or part < 0):
        return _invalid()
    if "voice_key" in payload and (not isinstance(voice_key, str) or not re.fullmatch(r"[0-9a-f]{32}", voice_key)):
        return _invalid()
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{32}", revision):
        return _invalid()

    if not request.session.get("reception"):
        return HttpResponse(status=404)
    conversation = Conversation.objects.filter(session_id=request.session.session_key)
    state = conversation.values_list("state", flat=True).first()
    if state is None:
        return HttpResponse(status=404)
    if state["revision"] != revision:
        return HttpResponse(status=409)
    parts = ()
    if part is not None:
        parts = _browser_voice_parts(state, overrides=pinned_overrides(state))
    if voice_key is not None and _voice_key(parts) != voice_key:
        return HttpResponse(status=409)
    if part is not None and part >= len(parts):
        return _invalid()
    busy = False
    try:
        if part is not None:
            segment = parts[part]
            speech = synthesize(segment["text"], segment.get("language", state["language"]))
        elif state["stage"] == "welcome":
            speech = synthesize_welcome(WELCOME_PARTS)
        else:
            speech = synthesize(state["reply"], state["language"])
    except SpeechBusy:
        speech, busy = None, True
    except SpeechUnavailable:
        speech = None
    if not conversation.filter(state__revision=revision).exists():
        return HttpResponse(status=409)
    if busy:
        return JsonResponse({"error": "speech_busy"}, status=429, headers={"Retry-After": "1"})
    if speech is None:
        return HttpResponse(status=503)
    return HttpResponse(speech, content_type="audio/wav", headers={"X-Content-Type-Options": "nosniff"})


@never_cache
@require_POST
def transcript(request):
    if not (settings.HOTEL_ASR_URL or settings.HOTEL_SPEECH_ROUTE_TARGETS):
        return HttpResponse(status=503)
    if request.content_type != "application/octet-stream":
        return _invalid(415)
    revision = request.headers.get("X-Reception-Revision", "")
    if not re.fullmatch(r"[0-9a-f]{32}", revision):
        return _invalid()

    if not request.session.get("reception"):
        return HttpResponse(status=404)
    conversation = Conversation.objects.filter(session_id=request.session.session_key)
    state = conversation.values_list("state", flat=True).first()
    if state is None:
        return HttpResponse(status=404)
    if state["revision"] != revision or state["stage"] in (*TERMINAL_STAGES, DELIVERY_PENDING):
        return HttpResponse(status=409)

    try:
        pcm = request.read(MAX_PCM_BYTES + 1)
    except UnreadablePostError:
        return _invalid()
    if len(pcm) > MAX_PCM_BYTES:
        return _invalid(413)
    if not pcm or len(pcm) % 2:
        return _invalid()

    busy = False
    try:
        if state["stage"] == "welcome":
            text = transcribe_welcome(pcm, state["language"])
        else:
            text = transcribe(pcm, state["language"])
    except SpeechBusy:
        text, busy = None, True
    except SpeechUnavailable:
        text = None
    current = conversation.filter(state__revision=revision)
    if not current.exists():
        return HttpResponse(status=409)
    if busy:
        return JsonResponse({"error": "speech_busy"}, status=429, headers={"Retry-After": "1"})
    if text is None:
        return HttpResponse(status=503)
    return JsonResponse({"text": text})
