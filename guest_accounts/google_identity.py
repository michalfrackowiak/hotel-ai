import re
from types import SimpleNamespace

from django.core.cache import cache
from google.auth import exceptions
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from jwt.exceptions import PyJWTError
from requests.utils import parse_dict_header


GOOGLE_CERTIFICATES_CACHE = "guest-google-certificates-v1"
GOOGLE_TRANSPORT_TIMEOUT = (2, 3)
GOOGLE_CERTIFICATES_URL = "https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_SUBJECT = re.compile(r"[\x21-\x7e]{1,255}")


class InvalidCredential(Exception):
    pass


class VerificationUnavailable(Exception):
    pass


def _certificate_max_age(headers):
    directives = parse_dict_header(headers.get("Cache-Control", "").lower())
    if directives.keys() & {"no-cache", "no-store", "private"}:
        return 0
    try:
        max_age = int(directives.get("max-age", 0))
        age = int(headers.get("Age", 0))
    except (TypeError, ValueError):
        return 0
    return max(0, max_age - max(0, age))


def _certificates(url, method="GET", **_kwargs):
    if url != GOOGLE_CERTIFICATES_URL or method != "GET":
        raise exceptions.TransportError("Unexpected Google certificate request.")
    cached = cache.get(GOOGLE_CERTIFICATES_CACHE)
    if cached is not None:
        return SimpleNamespace(status=200, data=cached)
    with Request() as request:
        response = request(url, timeout=GOOGLE_TRANSPORT_TIMEOUT, allow_redirects=False)
    if response.status == 200 and (max_age := _certificate_max_age(response.headers)):
        cache.set(GOOGLE_CERTIFICATES_CACHE, response.data, timeout=max_age)
    return response


def verify_google_credential(credential, client_id):
    try:
        claims = id_token.verify_oauth2_token(credential, _certificates, audience=client_id)
    except exceptions.TransportError as exc:
        raise VerificationUnavailable from exc
    except (exceptions.GoogleAuthError, PyJWTError, ValueError, TypeError, AttributeError, KeyError) as exc:
        raise InvalidCredential from exc
    subject = claims.get("sub")
    nonce = claims.get("nonce")
    if not isinstance(subject, str) or not GOOGLE_SUBJECT.fullmatch(subject) or not isinstance(nonce, str):
        raise InvalidCredential
    return subject, nonce
