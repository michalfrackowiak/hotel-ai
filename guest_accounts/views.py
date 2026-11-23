import json
import re
from datetime import timedelta
from secrets import token_urlsafe

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Exists, F
from django.db.models.functions import Now
from django.http import JsonResponse
from django.http.request import UnreadablePostError, split_domain_port
from django.middleware.csrf import rotate_token
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from reception.session_continuity import preserve_conversation_for_rotation

from .google_identity import InvalidCredential, VerificationUnavailable, verify_google_credential
from .models import GoogleLoginChallenge, GuestAccount


GUEST_SESSION_KEY = "hotel_guest_account_id"
GUEST_SESSION_AGE = timedelta(hours=8)
CHALLENGE_AGE = timedelta(minutes=5)
MAX_CHALLENGE_ATTEMPTS = 5
MAX_CREDENTIAL_BYTES = 4096
MAX_BODY_BYTES = MAX_CREDENTIAL_BYTES + 128
GOOGLE_CLIENT_ID = re.compile(r"[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com")
JWT_SHAPE = re.compile(r"[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+")


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _is_guest_host(request):
    if not settings.HOTEL_GOOGLE_GUEST_HOST:
        return False
    host, _ = split_domain_port(request.get_host())
    return host == settings.HOTEL_GOOGLE_GUEST_HOST


def google_client_id(request):
    client_id = settings.HOTEL_GOOGLE_CLIENT_ID
    if (
        settings.HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED
        and GOOGLE_CLIENT_ID.fullmatch(client_id)
        and _is_guest_host(request)
    ):
        return client_id
    return None


def _access_error(request):
    if not google_client_id(request):
        return _error("guest_sign_in_unavailable", 404)
    if request.user.is_authenticated:
        return _error("staff_session", 403)
    return None


def _live_session(session_key):
    return Session.objects.filter(pk=session_key, expire_date__gt=Now())


def _active_challenges(session_key):
    return GoogleLoginChallenge.objects.filter(
        session_id=session_key, session__expire_date__gt=Now(), expires_at__gt=Now(),
    )


def _credential(request):
    if request.content_type != "application/json":
        return None
    try:
        body = request.read(MAX_BODY_BYTES + 1)
        payload = json.loads(body) if len(body) <= MAX_BODY_BYTES else None
    except (UnreadablePostError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or payload.keys() != {"credential"}:
        return None
    credential = payload["credential"]
    if isinstance(credential, str) and len(credential) <= MAX_CREDENTIAL_BYTES and JWT_SHAPE.fullmatch(credential):
        return credential
    return None


def guest_signed_in(session):
    account_id = session.get(GUEST_SESSION_KEY)
    return type(account_id) is int and GuestAccount.objects.filter(
        Exists(_live_session(session.session_key)), pk=account_id,
    ).exists()


@never_cache
@require_safe
def session_status(request):
    if error := _access_error(request):
        return error
    return JsonResponse({"signed_in": guest_signed_in(request.session)})


@never_cache
@require_POST
@transaction.atomic
def google_nonce(request):
    if error := _access_error(request):
        return error
    session = request.session
    if session.session_key is None:
        session.create()
    if not _live_session(session.session_key).exists():
        return _error("challenge_missing", 409)
    challenge = _active_challenges(session.session_key).first()
    if challenge is None:
        challenge, _ = GoogleLoginChallenge.objects.update_or_create(
            session_id=session.session_key,
            defaults={"nonce": token_urlsafe(32), "expires_at": timezone.now() + CHALLENGE_AGE, "attempts": 0},
        )
    if challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
        return _error("challenge_exhausted", 429)
    return JsonResponse({"client_id": google_client_id(request), "nonce": challenge.nonce})


@transaction.atomic
def _reserve_attempt(session_key):
    challenges = _active_challenges(session_key)
    nonce = challenges.values_list("nonce", flat=True).first()
    if nonce is None:
        return None, _error("challenge_missing", 409)
    if not challenges.filter(nonce=nonce, attempts__lt=MAX_CHALLENGE_ATTEMPTS).update(attempts=F("attempts") + 1):
        return None, _error("challenge_exhausted", 429)
    return nonce, None


@transaction.atomic
def _finish_sign_in(request, subject, nonce):
    session = request.session
    if not _active_challenges(session.session_key).filter(nonce=nonce).delete()[0]:
        return _error("challenge_missing", 409)
    account, _ = GuestAccount.objects.get_or_create(google_subject=subject)
    previous_account = session.get(GUEST_SESSION_KEY)
    if previous_account is None or previous_account == account.pk:
        draft_preserved = preserve_conversation_for_rotation(session)
    else:
        session.flush()
        draft_preserved = False
    session[GUEST_SESSION_KEY] = account.pk
    session.set_expiry(timezone.now() + GUEST_SESSION_AGE)
    session.cycle_key()
    rotate_token(request)
    return JsonResponse({"signed_in": True, "draft_preserved": draft_preserved})


@never_cache
@require_POST
def google_sign_in(request):
    if error := _access_error(request):
        return error
    credential = _credential(request)
    if credential is None:
        return _error("invalid_request", 400)
    nonce, error = _reserve_attempt(request.session.session_key)
    if error:
        return error
    try:
        subject, returned_nonce = verify_google_credential(credential, google_client_id(request))
    except InvalidCredential:
        return _error("invalid_credential", 401)
    except VerificationUnavailable:
        return _error("verification_unavailable", 503)
    if not constant_time_compare(nonce, returned_nonce):
        return _error("invalid_credential", 401)
    return _finish_sign_in(request, subject, nonce)


@never_cache
@require_POST
@transaction.atomic
def sign_out(request):
    if not _is_guest_host(request):
        return _error("guest_sign_in_unavailable", 404)
    if request.user.is_authenticated:
        return _error("staff_session", 403)
    if GUEST_SESSION_KEY in request.session:
        request.session.flush()
        rotate_token(request)
    elif not google_client_id(request):
        return _error("guest_sign_in_unavailable", 404)
    return JsonResponse({"signed_in": False})
