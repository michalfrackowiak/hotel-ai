import json
import re
from datetime import date
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_safe

from model_providers.extraction import extract_stay_value

from .contacts import parse_email
from .conversation import TERMINAL_STAGES, advance, new_conversation, selects_english
from .models import Conversation, StaffHandover
from .parsing import parse_stay_input
from .speech import MAX_PCM_BYTES, SpeechUnavailable, synthesize, transcribe


MAX_BODY_BYTES = 4096
MAX_AUDIO_REQUEST_BYTES = 128
MAX_MESSAGE_LENGTH = 500
STAY_STAGES = ("check_in", "check_out", "guests")
HANDOVER_PAGE_SIZE = 25


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
    })


def _result(state, *, stale=False, cursor=0):
    payload = {field: state[field] for field in ("language", "stage", "revision", "reply")}
    if state["stage"] == "completed":
        payload["reference"] = state["reference"]
    if state["stage"] == "handover":
        handover = StaffHandover.objects.get(pk=state["handover_id"])
        payload["handover_closed"] = handover.closed
        payload["handover_messages"] = list(
            handover.messages.filter(id__gt=cursor).order_by("id").values("id", "staff", "text")[:HANDOVER_PAGE_SIZE]
        )
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
        elif not (poll or timeout):
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
        if preview["stage"] != "handover" or preview["language"] != language:
            return _result(preview, stale=True)
        return _result(preview, stale=preview["revision"] != revision, cursor=cursor)
    parsed = None
    if (
        turn and preview and preview["revision"] == revision and preview["language"] == language
        and preview["stage"] in STAY_STAGES
    ):
        check_in = date.fromisoformat(preview["check_in"]) if preview["stage"] == "check_out" else None
        parsed = parse_stay_input(preview["stage"], text, language, check_in=check_in)
        if parsed is None and not (preview["stage"] == "check_in" and language == "pl" and selects_english(text)):
            parsed = extract_stay_value(preview["stage"], text, language)

    # Recheck the revision under SQLite's write lock after parsing. Booking and
    # conversation updates commit together, including a retried confirmation.
    with transaction.atomic():
        request.session.setdefault("reception", True)
        if request.session.session_key is None:
            request.session.create()
        conversation, _ = Conversation.objects.get_or_create(
            session_id=request.session.session_key, defaults={"state": new_conversation(language)}
        )
        state = conversation.state
        if reset or (start and state["language"] != language):
            if state["stage"] == "handover":
                StaffHandover.objects.filter(pk=state["handover_id"]).update(closed=True)
            state = new_conversation(language)
        elif start:
            return _result(state)
        elif state["revision"] != revision or state["language"] != language:
            return _result(state, stale=True)
        elif state["stage"] == "handover":
            handover = StaffHandover.objects.get(pk=state["handover_id"])
            if turn:
                if handover.closed:
                    return _result(state, stale=True)
                handover.messages.create(text=text)
                state = {**state, "revision": uuid4().hex}
        elif timeout and state["stage"] != "check_in":
            return _result(state, stale=True)
        else:
            if voice and state["stage"] == "email" and state.get("flow") != "neptun":
                text = parse_email(text) or text
            state = advance(state, text or "", parsed, voice=voice)
            if state["stage"] == "handover":
                handover = StaffHandover.objects.create(conversation=conversation, language=state["language"])
                if text:
                    handover.messages.create(text=text)
                state["handover_id"] = handover.pk
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
    if state["revision"] != revision or state["stage"] in TERMINAL_STAGES:
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
