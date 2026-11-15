import json
import re

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_safe

from model_providers.extraction import extract_stay_value

from .conversation import advance, new_conversation
from .models import Conversation
from .parsing import parse_stay_input
from .speech import MAX_PCM_BYTES, SpeechUnavailable, synthesize, transcribe


MAX_BODY_BYTES = 4096
MAX_AUDIO_REQUEST_BYTES = 128
MAX_MESSAGE_LENGTH = 500
STAY_STAGES = ("check_in", "check_out", "guests")


@never_cache
@ensure_csrf_cookie
@require_safe
def index(request):
    language = "en" if request.GET.get("lang") == "en" else "pl"
    request.session.setdefault("reception", True)
    return render(request, "reception/index.html", {
        "language": language,
        "piper_enabled": bool(settings.HOTEL_PIPER_URL),
        "asr_enabled": bool(settings.HOTEL_ASR_URL),
    })


def _result(state, *, stale=False):
    payload = {field: state[field] for field in ("language", "stage", "revision", "reply")}
    if state["stage"] == "completed":
        payload["reference"] = state["reference"]
    if stale:
        payload["stale"] = True
    return JsonResponse(payload, status=409 if stale else 200)


def _invalid(status=400):
    return JsonResponse({"error": "invalid_message"}, status=status)


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
    text = payload.get("message")
    revision = payload.get("revision")
    if not (start or reset):
        if not (
            payload.keys() == {"language", "message", "revision"}
            and isinstance(text, str) and 1 <= len(text.strip()) <= MAX_MESSAGE_LENGTH
            and isinstance(revision, str) and len(revision) == 32
        ):
            return _invalid()
        text = text.strip()

    parsed = None
    if not (start or reset) and request.session.session_key:
        preview = Conversation.objects.filter(session_id=request.session.session_key).values_list("state", flat=True).first()
        if preview and preview["revision"] == revision and preview["language"] == language and preview["stage"] in STAY_STAGES:
            parsed = parse_stay_input(preview["stage"], text, language)
            if parsed is None:
                parsed = extract_stay_value(preview["stage"], text, language)

    # Recheck the revision under SQLite's write lock after parsing. Booking and
    # conversation updates commit together, including a retried confirmation.
    with transaction.atomic():
        request.session.setdefault("reception", True)
        if request.session.session_key is None:
            request.session.create()
        conversation, _ = Conversation.objects.get_or_create(
            session_id=request.session.session_key, defaults={"state": new_conversation(language, scenario=True)}
        )
        state = conversation.state
        if reset or (start and state["language"] != language):
            state = new_conversation(language, scenario=True)
        elif start:
            return _result(state)
        elif state["revision"] != revision or state["language"] != language:
            return _result(state, stale=True)
        else:
            state = advance(state, text, parsed)
        if state != conversation.state:
            conversation.state = state
            conversation.save(update_fields=["state"])
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
    if not isinstance(payload, dict) or payload.keys() != {"revision"}:
        return _invalid()
    revision = payload["revision"]
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
    try:
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
    if state["revision"] != revision or state["stage"] in ("completed", "cancelled"):
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
        text = transcribe(pcm, state["language"])
    except SpeechUnavailable:
        text = None
    if not conversation.filter(state__revision=revision).exists():
        return HttpResponse(status=409)
    if text is None:
        return HttpResponse(status=503)
    return JsonResponse({"text": text})
