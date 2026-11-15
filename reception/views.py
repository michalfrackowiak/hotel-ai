import json
import re
from datetime import date
from hashlib import sha256
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import render
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_safe

from model_providers.extraction import extract_stay_value

from .contacts import parse_email
from .conversation import COPY as BOOKING_COPY
from .conversation import TERMINAL_STAGES, WELCOME_PARTS, advance, new_conversation, recognizes_booking_answer, selects_english
from .models import Conversation, StaffHandover
from .parsing import parse_stay_input
from .receipt import DELIVERY_PENDING, refresh_receipt
from .scenario import ENGLISH_WEB_GREETING, render_chat, scenario_overrides, set_prompt, voice_segments
from .speech import MAX_PCM_BYTES, SpeechUnavailable, synthesize, synthesize_welcome, transcribe, transcribe_welcome
from .speech_feedback import corrected_booking_transcript
from .speech_training import TRAINABLE_STAGES, record_failed_transcript


MAX_BODY_BYTES = 4096
MAX_AUDIO_REQUEST_BYTES = 128
MAX_MESSAGE_LENGTH = 500
STAY_STAGES = ("check_in", "check_out", "guests")
HANDOVER_PAGE_SIZE = 25
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
def index(request):
    language = request.GET.get("lang")
    if language not in ("en", "pl"):
        language = "pl"
        if request.session.session_key:
            state = Conversation.objects.filter(session_id=request.session.session_key).values_list("state", flat=True).first()
            if state:
                language = state["language"]
    request.session.setdefault("reception", True)
    return render(request, "reception/index.html", {
        "language": language,
        "piper_enabled": bool(settings.HOTEL_PIPER_URL),
        "asr_enabled": bool(settings.HOTEL_ASR_URL),
        "speech_training_enabled": bool(settings.HOTEL_ASR_URL and settings.HOTEL_SPEECH_TRAINING_ENABLED),
    })


def _result(state, *, stale=False, cursor=0):
    payload = {field: state[field] for field in ("language", "stage", "revision", "reply")}
    overrides = scenario_overrides(state)
    payload["reply"] = render_chat(state, overrides=overrides)
    if state["stage"] in ("completed", DELIVERY_PENDING):
        payload["reference"] = state["reference"]
    if state["stage"] == "welcome":
        payload["welcome_parts"] = WELCOME_PARTS
    if parts := voice_segments(state, overrides=overrides):
        payload["voice_parts"] = parts
        payload["voice_key"] = _voice_key(parts)
        if state["stage"] == "check_in" and any(part["listen"] for part in parts):
            payload["arrival_parts"] = parts
    elif payload["reply"] != state["reply"]:
        payload["voice_reply"] = state["reply"]
    if state["stage"] == "check_in" and state["language"] == "pl" and state.get("voice_welcome"):
        payload["voice_welcome"] = True
    if state["stage"] == "handover":
        handover = StaffHandover.objects.get(pk=state["handover_id"])
        payload["handover_closed"] = handover.closed
        payload["voice_welcome"] = not handover.closed and not handover.messages.exists()
        payload["handover_messages"] = list(
            handover.messages.filter(id__gt=cursor).order_by("id").values("id", "staff", "text")[:HANDOVER_PAGE_SIZE]
        )
    if stale:
        payload["stale"] = True
    return JsonResponse(payload, status=409 if stale else 200)


def _invalid(status=400):
    return JsonResponse({"error": "invalid_message"}, status=status)


def _voice_key(parts):
    return sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _new_web_conversation(language):
    if language == "pl":
        return new_conversation(language)
    state = new_conversation("en", "check_in")
    set_prompt(state, "web_check_in", reply=f"{ENGLISH_WEB_GREETING} {state['reply']}")
    return state


def _save_state(conversation, state):
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
    state = refresh_receipt(state)
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
    start = payload.get("start") is True and payload.keys() == {"language", "start"}
    reset = payload.get("reset") is True and payload.keys() == {"language", "reset"}
    voice_start = payload.get("voice_start") is True and payload.keys() == {"language", "voice_start", "revision"}
    poll = payload.get("poll") is True and payload.keys() == {"language", "poll", "revision", "cursor"}
    voice = payload.get("voice") is True
    required_timeout_fields = {"language", "timeout", "revision"}
    timeout = payload.get("timeout") is True and (
        payload.keys() == required_timeout_fields or (voice and payload.keys() == required_timeout_fields | {"voice"})
    )
    cursor = payload.get("cursor", 0)
    text = payload.get("message")
    revision = payload.get("revision")
    required_turn_fields = {"language", "message", "revision"}
    turn = payload.keys() == required_turn_fields or (voice and payload.keys() == required_turn_fields | {"voice"})
    if not (start or reset):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{32}", revision):
            return _invalid()
        if turn:
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_MESSAGE_LENGTH:
                return _invalid()
            text = text.strip()
        elif not (poll or timeout or voice_start):
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
        and preview["stage"] in (*STAY_STAGES, "welcome")
    ):
        stage = "check_in" if preview["stage"] == "welcome" else preview["stage"]
        check_in = date.fromisoformat(preview["check_in"]) if stage == "check_out" else None
        if not (stage == "check_in" and language == "pl" and selects_english(text)):
            parsed = parse_stay_input(stage, text, language, check_in=check_in)
            if parsed is None:
                parsed = extract_stay_value(stage, text, language)

    # Recheck the revision under SQLite's write lock after parsing. Booking and
    # conversation updates commit together, including a retried confirmation.
    with transaction.atomic():
        request.session.setdefault("reception", True)
        if request.session.session_key is None:
            request.session.create()
        conversation, _ = Conversation.objects.get_or_create(
            session_id=request.session.session_key, defaults={"state": _new_web_conversation(language)}
        )
        state = conversation.state
        if reset or (start and state["language"] != language):
            if state["stage"] == "handover":
                StaffHandover.objects.filter(pk=state["handover_id"]).update(closed=True)
            state = _new_web_conversation(language)
        elif start:
            old_arrival = state["stage"] == "check_in" and state["reply"].startswith(OLD_ARRIVAL_PROMPTS)
            english_welcome = language == "en" and state["stage"] == "welcome"
            if not (old_arrival or english_welcome):
                return _result(state)
            state = _new_web_conversation(language)
        elif state["revision"] != revision or state["language"] != language:
            return _result(state, stale=True)
        elif voice_start:
            if language == "pl" and state["stage"] == "check_in" and state.get("voice_welcome"):
                state = _new_web_conversation(language)
            elif state["stage"] == "handover":
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
            state = advance(state, text or "", parsed, voice=voice)
            if was_welcome and state["stage"] == "check_in" and state["language"] == "pl" and not voice:
                state["voice_welcome"] = True
            if state["stage"] == "handover":
                handover = StaffHandover.objects.create(conversation=conversation, language=state["language"])
                if text:
                    handover.messages.create(text=text)
                state["handover_id"] = handover.pk
        _save_state(conversation, state)
        return _result(state)


@never_cache
@require_POST
def audio(request):
    if not settings.HOTEL_PIPER_URL:
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
    parts = voice_segments(state) if part is not None else ()
    if voice_key is not None and _voice_key(parts) != voice_key:
        return HttpResponse(status=409)
    if part is not None and part >= len(parts):
        return _invalid()
    try:
        if part is not None:
            segment = parts[part]
            speech = synthesize(segment["text"], segment.get("language", state["language"]))
        elif state["stage"] == "welcome":
            speech = synthesize_welcome(WELCOME_PARTS)
        else:
            speech = synthesize(state["reply"], state["language"])
    except SpeechUnavailable:
        speech = None
    if not conversation.filter(state__revision=revision).exists():
        return HttpResponse(status=409)
    if speech is None:
        return HttpResponse(status=503)
    return HttpResponse(speech, content_type="audio/wav", headers={"X-Content-Type-Options": "nosniff"})


@never_cache
@require_POST
def transcript(request):
    if not settings.HOTEL_ASR_URL:
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

    try:
        if state["stage"] == "welcome":
            text = transcribe_welcome(pcm, state["language"])
        else:
            text = transcribe(pcm, state["language"])
    except SpeechUnavailable:
        text = None
    current = conversation.filter(state__revision=revision)
    if (
        text is not None and settings.HOTEL_SPEECH_TRAINING_ENABLED
        and state["stage"] in TRAINABLE_STAGES and not recognizes_booking_answer(state, text)
    ):
        original = text
        text = corrected_booking_transcript(state, original)
        share_clip = request.headers.get("X-Reception-Speech-Training") == "1"
        if share_clip and any(pcm):
            with transaction.atomic():
                if not current.exists():
                    return HttpResponse(status=409)
                record_failed_transcript(
                    turn_key=salted_hmac("reception.speech_training", revision, algorithm="sha256").hexdigest(),
                    language=state["language"], stage=state["stage"], pcm=pcm, transcript=original,
                )
            return JsonResponse({"text": text})
    if not current.exists():
        return HttpResponse(status=409)
    if text is None:
        return HttpResponse(status=503)
    return JsonResponse({"text": text})
