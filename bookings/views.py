import json
import re
from datetime import date
from decimal import Decimal
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

from .models import CURRENCY
from .services import BookingError, available_rooms, confirm_booking


MAX_BOOKING_BODY_BYTES = 8192
BOOKING_FIELDS = {"room_id", "check_in", "check_out", "guests", "name", "email", "nightly_rate", "idempotency_key"}


def _error(code, message, status, headers=None):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status, headers=headers)


def _booking_api(method):
    def decorate(view):
        @wraps(view)
        def authenticated(request):
            scheme, _, token = request.headers.get("Authorization", "").partition(" ")
            key = settings.BOOKING_API_KEY
            if not key or scheme.lower() != "bearer" or not constant_time_compare(token, key):
                return _error("unauthorized", "A booking API key is required.", 401, {"WWW-Authenticate": "Bearer"})
            if request.method != method:
                return _error("method_not_allowed", f"Use {method} for this endpoint.", 405, {"Allow": method})
            try:
                return view(request)
            except BookingError as error:
                return _error(error.code, error.message, error.status)

        return authenticated

    return decorate


def _date(value):
    if isinstance(value, str) and len(value) == 10:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise BookingError("invalid_dates", "Use YYYY-MM-DD for check-in and check-out.")


def _booking_payload(request):
    if request.content_type != "application/json":
        raise BookingError("unsupported_media_type", "Use application/json for bookings.", 415)
    body = request.read(MAX_BOOKING_BODY_BYTES + 1)
    if len(body) > MAX_BOOKING_BODY_BYTES:
        raise BookingError("payload_too_large", "The booking request is too large.", 413)
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_invalid_json_constant)
    except (ValueError, RecursionError) as error:
        raise BookingError("invalid_json", "Enter a valid JSON booking object.") from error
    if not isinstance(payload, dict) or payload.keys() != BOOKING_FIELDS:
        raise BookingError("invalid_payload", "Provide all eight booking fields.")
    return payload


def _invalid_json_constant(value):
    raise ValueError(f"Invalid JSON number {value}")


def _rate(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,2})?", value):
        return Decimal(value)
    raise BookingError("invalid_nightly_rate", "Send the quoted nightly rate as a decimal string.")


def _idempotency_key(value):
    if isinstance(value, str) and len(value) == 36:
        try:
            parsed = UUID(value)
            if str(parsed) == value.lower():
                return parsed
        except ValueError:
            pass
    raise BookingError("invalid_idempotency_key", "Idempotency key must be a UUID.")


@_booking_api("GET")
def availability(request):
    guests = request.GET.get("guests", "")
    if not guests.isascii() or not guests.isdecimal() or len(guests) > 5:
        raise BookingError("invalid_guests", "Guests must be a positive whole number.")
    offers = available_rooms(
        check_in=_date(request.GET.get("check_in")),
        check_out=_date(request.GET.get("check_out")),
        guests=int(guests),
    )
    return JsonResponse({"rooms": [
        {
            "room_id": offer.room_id,
            "room_code": offer.room_code,
            "capacity": offer.capacity,
            "nightly_rate": f"{offer.nightly_rate:.2f}",
            "total_price": f"{offer.total_price:.2f}",
            "currency": CURRENCY,
        }
        for offer in offers
    ]})


# Only the bearer credential authorizes this endpoint. Browser sessions use a separate CSRF-protected view.
@csrf_exempt
@_booking_api("POST")
def create_booking(request):
    payload = _booking_payload(request)
    booking, created = confirm_booking(
        room_id=payload["room_id"],
        check_in=_date(payload["check_in"]),
        check_out=_date(payload["check_out"]),
        guests=payload["guests"],
        name=payload["name"],
        email=payload["email"],
        nightly_rate=_rate(payload["nightly_rate"]),
        idempotency_key=_idempotency_key(payload["idempotency_key"]),
    )
    return JsonResponse({"booking": {
        "reference": str(booking.reference),
        "room_id": booking.room_id,
        "room_code": booking.room.code,
        "check_in": booking.check_in.isoformat(),
        "check_out": booking.check_out.isoformat(),
        "guests": booking.guests,
        "nightly_rate": f"{booking.nightly_rate:.2f}",
        "total_price": f"{booking.total_price:.2f}",
        "currency": CURRENCY,
    }}, status=201 if created else 200)
