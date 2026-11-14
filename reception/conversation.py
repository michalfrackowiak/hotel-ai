from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from bookings.services import BookingError, available_rooms, confirm_booking


COPY = {
    "en": {
        "welcome": "Hello! When would you like to check in? You can say tomorrow or give a date.",
        "check_in": "What is your check-in date? Please choose today or a later date.",
        "ask_check_out": (
            "How long will you be staying with us, or when is your planned check-out date? "
            "You can give a date or say when you’ll check out, for example, in three days."
        ),
        "check_out": "Please give a checkout date after check-in. For example, you can say in three days.",
        "ask_guests": "Check-in is {check_in} and checkout is {check_out}. How many guests are staying?",
        "guests": "Please enter the number of guests as a positive whole number.",
        "ask_name": "Room {room} is available for {rate} PLN per night, {total} PLN for the stay. What name should I put on the booking?",
        "name": "Please enter a guest name of up to 150 characters.",
        "ask_email": "What is your email address?",
        "email": "Please enter a valid email address, for example guest@example.com.",
        "quote": "Room {room} for a party of {guests}, from {check_in} to {check_out}. The rate is {rate} PLN per night and the total is {total} PLN. Reply Yes to book or No to cancel.",
        "confirmed": "Your booking is confirmed. Your reference is {reference}.",
        "cancelled": "I've cancelled this request. What date would you like to check in instead?",
        "unavailable": "No room is available for that stay. What check-in date would you like to try?",
        "offer_changed": "That room or rate has changed. Please enter your check-in date to request a fresh quote.",
    },
    "pl": {
        "welcome": "Dzień dobry! Kiedy chcesz się zameldować? Możesz powiedzieć jutro lub podać datę.",
        "check_in": "Kiedy chcesz się zameldować? Podaj dzisiejszą lub późniejszą datę.",
        "ask_check_out": (
            "Jak długo planujesz u nas zostać albo kiedy chcesz się wymeldować? "
            "Możesz podać datę lub powiedzieć na przykład za trzy dni."
        ),
        "check_out": "Podaj datę wymeldowania po zameldowaniu. Możesz na przykład powiedzieć za trzy dni.",
        "ask_guests": "Zameldowanie {check_in}, wymeldowanie {check_out}. Ilu gości będzie nocować?",
        "guests": "Podaj liczbę gości jako dodatnią liczbę całkowitą.",
        "ask_name": "Pokój {room} jest dostępny w cenie {rate} PLN za noc, {total} PLN za cały pobyt. Na kogo mam zapisać rezerwację?",
        "name": "Podaj imię i nazwisko gościa, maksymalnie 150 znaków.",
        "ask_email": "Jaki jest Twój adres e-mail?",
        "email": "Podaj poprawny adres e-mail, na przykład gosc@example.com.",
        "quote": "Pokój {room}, liczba gości {guests}, pobyt od {check_in} do {check_out}. Cena wynosi {rate} PLN za noc i {total} PLN za cały pobyt. Odpowiedz Tak, aby zarezerwować, lub Nie, aby anulować.",
        "confirmed": "Twoja rezerwacja jest potwierdzona. Numer rezerwacji to {reference}.",
        "cancelled": "Anulowano zgłoszenie. Kiedy chcesz się zameldować?",
        "unavailable": "Brak dostępnych pokoi na ten pobyt. Jaką datę zameldowania chcesz sprawdzić?",
        "offer_changed": "Dostępność pokoju lub cena uległa zmianie. Podaj datę zameldowania, aby otrzymać nową ofertę.",
    },
}
CONFIRMATIONS = {"en": ("yes", "no"), "pl": ("tak", "nie")}


def new_conversation(language, prompt="welcome"):
    return {
        "language": language,
        "stage": "check_in",
        "revision": uuid4().hex,
        "reply": COPY[language][prompt],
    }


def _display_date(value, language):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if language == "pl" else value


def _price(value, language):
    amount = f"{value:.2f}"
    return amount.replace(".", ",") if language == "pl" else amount


def advance(state, message, parsed):
    language = state["language"]
    copy = COPY[language]
    stage = state["stage"]
    if stage == "completed":
        return state
    state = {**state, "revision": uuid4().hex}
    if stage != "confirm":
        state["reply"] = copy[stage]

    if stage == "check_in":
        if type(parsed) is date and parsed >= timezone.localdate():
            state.update(stage="check_out", check_in=parsed.isoformat(), reply=copy["ask_check_out"])
    elif stage == "check_out":
        if type(parsed) is date and parsed > date.fromisoformat(state["check_in"]):
            check_out = parsed.isoformat()
            reply = copy["ask_guests"].format(
                check_in=_display_date(state["check_in"], language),
                check_out=_display_date(check_out, language),
            )
            state.update(stage="guests", check_out=check_out, reply=reply)
    elif stage == "guests":
        if type(parsed) is int and 1 <= parsed <= 32767:
            return _find_room(state, parsed)
    elif stage == "name":
        if 1 <= len(message) <= 150 and message.isprintable():
            state.update(stage="email", name=message, reply=copy["ask_email"])
    elif stage == "email":
        try:
            if len(message) > 254 or not message.isprintable():
                raise ValidationError("Invalid email")
            validate_email(message)
        except ValidationError:
            return state
        reply = copy["quote"].format(
            room=state["room"], guests=state["guests"],
            check_in=_display_date(state["check_in"], language),
            check_out=_display_date(state["check_out"], language),
            rate=_price(Decimal(state["nightly_rate"]), language),
            total=_price(Decimal(state["total_price"]), language),
        )
        state.update(stage="confirm", email=message, reply=reply)
    elif stage == "confirm":
        return _confirm(state, message)
    return state


def _find_room(state, guests):
    language = state["language"]
    try:
        offers = available_rooms(
            check_in=date.fromisoformat(state["check_in"]),
            check_out=date.fromisoformat(state["check_out"]),
            guests=guests,
        )
    except BookingError:
        return new_conversation(language, "offer_changed")
    if not offers:
        return new_conversation(language, "unavailable")
    offer = offers[0]
    state.update(
        stage="name", guests=guests, room_id=offer.room_id, room=offer.room_code,
        nightly_rate=str(offer.nightly_rate), total_price=str(offer.total_price),
        booking_key=str(uuid4()),
        reply=COPY[language]["ask_name"].format(
            room=offer.room_code, rate=_price(offer.nightly_rate, language), total=_price(offer.total_price, language),
        ),
    )
    return state


def _confirm(state, message):
    language = state["language"]
    yes, no = CONFIRMATIONS[language]
    answer = message.casefold().rstrip(".!").strip()
    if answer == no:
        return new_conversation(language, "cancelled")
    if answer != yes:
        return state
    try:
        booking, _ = confirm_booking(
            room_id=state["room_id"], check_in=date.fromisoformat(state["check_in"]),
            check_out=date.fromisoformat(state["check_out"]), guests=state["guests"],
            name=state["name"], email=state["email"], nightly_rate=Decimal(state["nightly_rate"]),
            idempotency_key=UUID(state["booking_key"]),
        )
    except BookingError:
        return new_conversation(language, "offer_changed")
    reference = str(booking.reference)
    return {
        "language": language, "stage": "completed", "revision": state["revision"], "reference": reference,
        "reply": COPY[language]["confirmed"].format(reference=reference),
    }
