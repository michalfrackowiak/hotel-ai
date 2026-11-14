from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import SuspiciousOperation, ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound, QueryDict
from django.http.request import UnreadablePostError
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from .calls import advance_call, is_call_sid, start_call


MAX_WEBHOOK_BODY_BYTES = 16 * 1024
CALL_FORM_FIELDS = ("CallSid", "Digits", "SpeechResult")
LOCALES = {"en": ("en-US", "Polly.Joanna"), "pl": ("pl-PL", "Polly.Ewa")}
TERMINAL_STAGES = {"completed", "ended"}
validate_https_url = URLValidator(schemes=["https"])


def _is_https_origin(value):
    try:
        validate_https_url(value)
        url = urlsplit(value)
        return (
            url.hostname is not None and url.username is None and url.password is None
            and url.path in ("", "/") and "?" not in value and "#" not in value
            and not url.netloc.endswith(":") and url.port != 0
        )
    except (ValidationError, ValueError):
        return False


def _public_url(path):
    return settings.HOTEL_TELEPHONY_PUBLIC_BASE_URL.rstrip("/") + path


def _twilio_post(view):
    @csrf_exempt
    @require_POST
    @never_cache
    @wraps(view)
    def signed(request, *args, **kwargs):
        auth_token = settings.HOTEL_TWILIO_AUTH_TOKEN
        if not auth_token.strip() or not _is_https_origin(settings.HOTEL_TELEPHONY_PUBLIC_BASE_URL):
            return HttpResponseNotFound()

        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature or request.META.get("QUERY_STRING"):
            return HttpResponseForbidden()
        if request.content_type != "application/x-www-form-urlencoded":
            return HttpResponse(status=415)
        try:
            body = request.read(MAX_WEBHOOK_BODY_BYTES + 1)
            if len(body) > MAX_WEBHOOK_BODY_BYTES:
                return HttpResponse(status=413)
            form = QueryDict(body, encoding="utf-8")
        except (SuspiciousOperation, UnreadablePostError):
            return HttpResponseBadRequest()

        # Twilio signs the external URL, which can differ from the internal proxy URL.
        if not RequestValidator(auth_token).validate(_public_url(request.path), form, signature):
            return HttpResponseForbidden()
        if any(len(form.getlist(field)) > 1 for field in CALL_FORM_FIELDS):
            return HttpResponseBadRequest()
        call_sid = form.get("CallSid", "")
        if not is_call_sid(call_sid):
            return HttpResponseBadRequest()
        return view(form, call_sid, *args, **kwargs)

    return signed


def _voice_reply(result):
    response = VoiceResponse()
    locale, voice = LOCALES[result.language]
    if result.stage in TERMINAL_STAGES:
        response.say(result.reply, language=locale, voice=voice)
        response.hangup()
    else:
        action = _public_url(reverse("telephony:turn", kwargs={"revision": result.revision}))
        attributes = {"action": action, "method": "POST", "timeout": 7, "action_on_empty_result": True}
        if result.stage == "language":
            gather = Gather(input="dtmf", num_digits=1, **attributes)
            gather.say(result.reply, language=locale, voice=voice)
            polish_locale, polish_voice = LOCALES["pl"]
            gather.say("Aby wybrać język polski, naciśnij dwa.", language=polish_locale, voice=polish_voice)
        else:
            gather = Gather(
                input="speech", language=locale, speech_model="experimental_utterances", speech_timeout=2,
                profanity_filter=False, **attributes,
            )
            gather.say(result.reply, language=locale, voice=voice)
        response.append(gather)
    return HttpResponse(str(response), content_type="application/xml; charset=utf-8")


@_twilio_post
def incoming(form, call_sid):
    return _voice_reply(start_call(call_sid))


@_twilio_post
def turn(form, call_sid, revision):
    return _voice_reply(advance_call(
        call_sid, revision, digit=form.get("Digits", ""), speech=form.get("SpeechResult", ""),
    ))
