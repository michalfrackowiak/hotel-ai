from functools import partial
import logging
from smtplib import SMTPException, SMTPRecipientsRefused, SMTPResponseException

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from requests.exceptions import RequestException
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client as TwilioClient

from .models import BookingConfirmation


outcome_logger = logging.getLogger("bookings.confirmation_outcomes")

COPY = {
    "en": (
        "Neptun Resort booking confirmation",
        "Booking {reference}\nRoom {room}, {check_in} to {check_out}, {guests} guests. Total {total:.2f} PLN.",
    ),
    "pl": (
        "Potwierdzenie rezerwacji w Resorcie Neptun",
        "Rezerwacja {reference}\nPokój {room}, od {check_in} do {check_out}, liczba gości {guests}. Razem {total:.2f} PLN.",
    ),
}


def configured_methods():
    methods = []
    if settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL:
        try:
            validate_email(settings.DEFAULT_FROM_EMAIL)
        except ValidationError:
            pass
        else:
            methods.append(BookingConfirmation.Method.EMAIL)
    if settings.HOTEL_TWILIO_ACCOUNT_SID and settings.HOTEL_TWILIO_AUTH_TOKEN and settings.HOTEL_TWILIO_SMS_FROM:
        methods.append(BookingConfirmation.Method.SMS)
    return methods


def _message(confirmation):
    booking = confirmation.booking
    subject, template = COPY[confirmation.language]
    date_format = "%d.%m.%Y" if confirmation.language == "pl" else "%Y-%m-%d"
    return subject, template.format(
        reference=booking.reference,
        room=booking.room.code,
        check_in=booking.check_in.strftime(date_format),
        check_out=booking.check_out.strftime(date_format),
        guests=booking.guests,
        total=booking.total_price,
    )


def _submit(confirmation, subject, body):
    if confirmation.method == BookingConfirmation.Method.EMAIL:
        count = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [confirmation.destination])
        return ("", count == 1)
    client = TwilioClient(
        settings.HOTEL_TWILIO_ACCOUNT_SID, settings.HOTEL_TWILIO_AUTH_TOKEN,
        http_client=TwilioHttpClient(timeout=10),
    )
    message = client.messages.create(
        to=confirmation.destination, from_=settings.HOTEL_TWILIO_SMS_FROM, body=f"{subject}\n{body}",
    )
    return message.sid, message.status not in ("failed", "undelivered", "canceled")


def dispatch_confirmations(methods, limit=50):
    status = BookingConfirmation.Status
    pending = BookingConfirmation.objects.filter(method__in=methods, status=status.PENDING)
    ids = list(pending.order_by("pk").values_list("pk", flat=True)[:limit])
    accepted = failed = 0
    for pk in ids:
        # Claim before network I/O. A worker crash leaves the attempt visible in admin
        # and prevents automatic resends when the provider may already have accepted it.
        if not pending.filter(pk=pk).update(status=status.SENDING, claimed_at=timezone.now()):
            continue
        confirmation = BookingConfirmation.objects.select_related("booking__room").get(pk=pk)
        subject, body = _message(confirmation)
        provider_reference = ""
        accepted_at = None
        try:
            provider_reference, submitted = _submit(confirmation, subject, body)
            outcome = status.ACCEPTED if submitted else status.FAILED
        except TwilioRestException as error:
            outcome = status.FAILED if 400 <= (error.status or 0) < 500 else status.UNCERTAIN
        except SMTPResponseException as error:
            outcome = status.FAILED if 400 <= error.smtp_code < 600 else status.UNCERTAIN
        except (SMTPRecipientsRefused, ValidationError, ValueError):
            outcome = status.FAILED
        except (SMTPException, RequestException, TwilioException, OSError):
            outcome = status.UNCERTAIN
        if outcome == status.ACCEPTED:
            accepted += 1
            accepted_at = timezone.now()
        else:
            failed += 1
        updated = BookingConfirmation.objects.filter(pk=pk).update(
            status=outcome, accepted_at=accepted_at, provider_reference=provider_reference,
        )
        if (
            updated
            and confirmation.method in BookingConfirmation.Method.values
            and confirmation.language in ("en", "pl")
        ):
            transaction.on_commit(partial(
                outcome_logger.info,
                "booking_confirmation_submission method=%s language=%s outcome=%s",
                confirmation.method, confirmation.language, outcome.value,
            ), robust=True)
    return accepted, failed
