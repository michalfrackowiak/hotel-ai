from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from bookings.confirmations import configured_methods
from bookings.models import BookingConfirmation

from .scenario import set_prompt


DELIVERY_PENDING = "delivery_pending"
DELIVERY_WAIT_SECONDS = 25
SENT = {
    "en": {
        "email": "Your reservation is fully confirmed. We have sent the details to your email address. Thank you for choosing Neptun Resort and have a wonderful stay!",
        "sms": "Your reservation is fully confirmed. We have sent a confirmation text message. Thank you for choosing Neptun Resort and see you soon!",
    },
    "pl": {
        "email": "Twoja rezerwacja została w pełni potwierdzona. Wysłaliśmy szczegóły na podany adres e-mail. Dziękujemy za wybór Resortu Neptun i życzymy miłego dnia!",
        "sms": "Twoja rezerwacja została w pełni potwierdzona. Wysłaliśmy SMS z potwierdzeniem. Dziękujemy za wybór Resortu Neptun i do zobaczenia!",
    },
}
COPY = {
    "en": {
        "waiting": "Your reservation is fully confirmed. Please wait while we send your confirmation.",
        "pending": "Your reservation is fully confirmed. Your {channel} confirmation is awaiting dispatch. Thank you for choosing Neptun Resort!",
        "unconfirmed": "Your reservation is fully confirmed. We could not verify that your {channel} confirmation was sent. Thank you for choosing Neptun Resort!",
        "reference": "Your reference is {reference}.",
        "email": "email",
        "sms": "SMS",
    },
    "pl": {
        "waiting": "Twoja rezerwacja została w pełni potwierdzona. Poczekaj na wysłanie potwierdzenia.",
        "pending": "Twoja rezerwacja została w pełni potwierdzona. Potwierdzenie {channel} oczekuje na wysłanie. Dziękujemy za wybór Resortu Neptun!",
        "unconfirmed": "Twoja rezerwacja została w pełni potwierdzona. Nie udało się potwierdzić wysłania potwierdzenia {channel}. Dziękujemy za wybór Resortu Neptun!",
        "reference": "Numer rezerwacji to {reference}.",
        "email": "e-mailem",
        "sms": "SMS-em",
    },
}


def waits_for_confirmation(method):
    return method in settings.HOTEL_CONFIRMATION_WAIT_METHODS or method in configured_methods()


def start_receipt(state, reference, method, *, wait=None):
    receipt = set_prompt({
        "language": state["language"], "revision": state["revision"], "flow": state["flow"],
        "stage": DELIVERY_PENDING, "reference": reference, "method": method,
    }, "receipt_waiting")
    if wait is None:
        wait = waits_for_confirmation(method)
    if wait:
        receipt["delivery_deadline"] = timezone.now().timestamp() + DELIVERY_WAIT_SECONDS
        return receipt
    return _complete(receipt, BookingConfirmation.Status.PENDING)


def refresh_receipt(state, *, resolve_transition=None):
    status = BookingConfirmation.Status
    outcome = BookingConfirmation.objects.filter(booking__reference=state["reference"]).values_list(
        "status", flat=True,
    ).first()
    if outcome in (status.PENDING, status.SENDING) and timezone.now().timestamp() < state["delivery_deadline"]:
        if resolve_transition is not None:
            resolve_transition(state, "retry")
        return state
    if resolve_transition is not None:
        event = {
            status.ACCEPTED: "dispatch_accepted",
            status.PENDING: "dispatch_pending",
        }.get(outcome, "dispatch_unverified")
        # The booking already exists. The resolver reports graph drift, and
        # the authoritative booking reference must still reach the guest.
        resolve_transition(state, event)
    return _complete({**state, "revision": uuid4().hex}, outcome)


def _complete(state, outcome):
    language = state["language"]
    method = state["method"]
    reference = state["reference"]
    copy = COPY[language]
    if outcome == BookingConfirmation.Status.ACCEPTED:
        key = f"receipt_sent_{method}"
    else:
        key = "receipt_pending" if outcome == BookingConfirmation.Status.PENDING else "receipt_unconfirmed"
    return set_prompt({
        "language": language, "revision": state["revision"], "flow": state["flow"],
        "stage": "completed", "reference": reference,
    }, key, {"channel": copy[method], "reference": reference}, tail="receipt_reference")
