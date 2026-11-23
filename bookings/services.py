import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator, validate_email
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import RATE_DIGITS, RATE_PLACES, Booking, BookingConfirmation, Client, Room


class BookingError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class RoomOffer:
    room_id: int
    room_code: str
    capacity: int
    nightly_rate: Decimal
    total_price: Decimal


def _validate_stay(check_in: date, check_out: date, guests: int) -> None:
    if type(check_in) is not date or type(check_out) is not date or check_out <= check_in:
        raise BookingError("invalid_dates", "Check-out must be after check-in.")
    if type(guests) is not int or not 1 <= guests <= 32767:
        raise BookingError("invalid_guests", "Guests must be a positive whole number.")


def _validate_check_in(check_in: date) -> None:
    if check_in < timezone.localdate():
        raise BookingError("invalid_dates", "Check-in must be today or later.")


def _available_rooms(check_in: date, check_out: date, guests: int):
    overlaps = Booking.objects.filter(
        room=OuterRef("pk"), check_in__lt=check_out, check_out__gt=check_in
    )
    return Room.objects.select_related("price").filter(
        ~Exists(overlaps), accepts_reservations=True, capacity__gte=guests, price__isnull=False
    )


def available_rooms(*, check_in: date, check_out: date, guests: int) -> list[RoomOffer]:
    _validate_stay(check_in, check_out, guests)
    _validate_check_in(check_in)
    nights = (check_out - check_in).days
    rooms = _available_rooms(check_in, check_out, guests).order_by("price__nightly_rate", "capacity", "code")
    return [
        RoomOffer(room.pk, room.code, room.capacity, room.price.nightly_rate, room.price.nightly_rate * nights)
        for room in rooms
    ]


def normalize_phone(value: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[+0-9().\s-]{1,50}", value):
        number = re.sub(r"[().\s-]", "", value)
        if number.startswith("00"):
            number = "+" + number[2:]
        elif re.fullmatch(r"[1-9][0-9]{8}", number):
            number = "+48" + number
        elif re.fullmatch(r"48[0-9]{9}", number):
            number = "+" + number
        if re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
            return number
    raise BookingError("invalid_phone", "Enter a valid phone number with its country code.")


def _validate_email(email):
    if not 1 <= len(email) <= 254 or not email.isprintable():
        raise BookingError("invalid_email", "Enter a valid email address.")
    try:
        validate_email(email)
    except ValidationError as error:
        raise BookingError("invalid_email", "Enter a valid email address.") from error


def _validate_contact(name, email, phone, delivery_method, language):
    name = name.strip() if isinstance(name, str) else ""
    email = email.strip() if isinstance(email, str) else ""
    if len(name) > 150 or not name.isprintable() or (not name and not delivery_method):
        raise BookingError("invalid_name", "Enter a guest name of up to 150 characters.")
    if delivery_method not in ("", *BookingConfirmation.Method.values):
        raise BookingError("invalid_delivery_method", "Choose email or SMS confirmation.")
    if delivery_method and language not in ("en", "pl"):
        raise BookingError("invalid_language", "Choose Polish or English confirmation.")
    if delivery_method == BookingConfirmation.Method.SMS:
        if email:
            raise BookingError("invalid_contact", "Provide only the contact for the selected confirmation method.")
        phone = normalize_phone(phone)
    else:
        if phone:
            raise BookingError("invalid_contact", "Provide only the contact for the selected confirmation method.")
        _validate_email(email)
        phone = ""
    return name, email, phone


def _validate_rate(nightly_rate: Decimal) -> None:
    message = "Enter a positive nightly rate with up to two decimal places."
    if not isinstance(nightly_rate, Decimal) or not nightly_rate.is_finite() or nightly_rate <= 0:
        raise BookingError("invalid_nightly_rate", message)
    try:
        DecimalValidator(RATE_DIGITS, RATE_PLACES)(nightly_rate)
    except ValidationError as error:
        raise BookingError("invalid_nightly_rate", message) from error


def confirm_booking(
    *,
    room_id: int,
    check_in: date,
    check_out: date,
    guests: int,
    name: str,
    email: str,
    nightly_rate: Decimal,
    idempotency_key: UUID,
    phone: str = "",
    delivery_method: str = "",
    language: str = "en",
) -> tuple[Booking, bool]:
    _validate_stay(check_in, check_out, guests)
    if type(room_id) is not int or not 1 <= room_id <= 2**63 - 1:
        raise BookingError("invalid_room_id", "Room ID must be a positive whole number.")
    if not isinstance(idempotency_key, UUID):
        raise BookingError("invalid_idempotency_key", "Idempotency key must be a UUID.")
    _validate_rate(nightly_rate)
    name, email, phone = _validate_contact(name, email, phone, delivery_method, language)

    # SQLite starts this transaction in IMMEDIATE mode to serialize availability checks and writes.
    with transaction.atomic():
        existing = Booking.objects.select_related("client", "room", "confirmation").filter(
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            confirmation = getattr(existing, "confirmation", None)
            booked_email = existing.client.email
            booked_phone = existing.client.phone
            if confirmation:
                booked_email = confirmation.destination if confirmation.method == BookingConfirmation.Method.EMAIL else ""
                booked_phone = confirmation.destination if confirmation.method == BookingConfirmation.Method.SMS else ""
            requested_stay = (
                room_id, check_in, check_out, guests, name, email, phone, nightly_rate,
                (delivery_method, language) if delivery_method else None,
            )
            booked_stay = (
                existing.room_id, existing.check_in, existing.check_out, existing.guests,
                existing.client.name, booked_email, booked_phone, existing.nightly_rate,
                (confirmation.method, confirmation.language) if confirmation else None,
            )
            if requested_stay != booked_stay:
                raise BookingError("idempotency_conflict", "This idempotency key belongs to a different booking.", 409)
            return existing, False

        _validate_check_in(check_in)
        room = _available_rooms(check_in, check_out, guests).filter(pk=room_id).first()
        if room is None:
            raise BookingError("room_unavailable", "The room is unavailable for this stay.", 409)
        if room.price.nightly_rate != nightly_rate:
            raise BookingError("stale_price", "The nightly rate changed. Request a new quote.", 409)

        client, _ = Client.objects.get_or_create(name=name, email=email, phone=phone)
        booking = Booking.objects.create(
            room=room,
            client=client,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            nightly_rate=room.price.nightly_rate,
            idempotency_key=idempotency_key,
        )
        if delivery_method:
            BookingConfirmation.objects.create(
                booking=booking, method=delivery_method, destination=email or phone, language=language,
            )
        return booking, True
