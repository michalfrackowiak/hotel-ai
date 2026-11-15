"""Booking turns for drafts begun before the complete Resort Neptun scenario."""

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
BROWSER_COPY = {
    "en": {
        "welcome": "Welcome to Resort Neptun Jastrzębia Góra. To check availability, prices, or make a reservation, please give your planned arrival date.",
        "ask_guests": "Check-in is {check_in} and checkout is {check_out}. How many guests will stay, including adults and children?",
        "select": "I found these rooms. {options} Which option would you like?",
        "choice": "Option {number}, {room}, for {total} PLN total.",
        "ask_name": "Thank you. What name should I put on the booking?",
        "ask_email": "Please say or type the email address for the booking.",
        "goodbye": "Thank you for contacting Resort Neptun. Goodbye!",
        "confirmed": "Your booking is confirmed. Your reference is {reference}. Thank you for choosing Resort Neptun!",
    },
    "pl": {
        "welcome": "Dzień dobry, Resort Neptun Jastrzębia Góra. For English, choose English or say English. Aby sprawdzić dostępność, ceny lub dokonać rezerwacji, podaj planowaną datę przyjazdu.",
        "ask_guests": "Zameldowanie {check_in}, wymeldowanie {check_out}. Podaj łączną liczbę dorosłych i dzieci.",
        "select": "Sprawdziłam dostępność. {options} Którą opcję wybierasz?",
        "choice": "Opcja {number}, {room}, za {total} PLN za pobyt.",
        "ask_name": "Dziękuję. Na kogo mam zapisać rezerwację?",
        "ask_email": "Podyktuj lub wpisz adres e-mail do rezerwacji.",
        "goodbye": "Dziękuję za kontakt z Resortem Neptun. Do widzenia!",
        "confirmed": "Twoja rezerwacja została potwierdzona. Numer rezerwacji to {reference}. Dziękujemy za wybór Resortu Neptun!",
    },
}


def new_conversation(language, prompt="welcome", *, scenario=False):
    state = {
        "language": language,
        "stage": "check_in",
        "revision": uuid4().hex,
        "reply": BROWSER_COPY[language].get(prompt, COPY[language][prompt]) if scenario else COPY[language][prompt],
    }
    if scenario:
        state["scenario"] = True
    return state


def _display_date(value, language):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if language == "pl" else value


def _price(value, language):
    amount = f"{value:.2f}"
    return amount.replace(".", ",") if language == "pl" else amount


def advance(state, message, parsed):
    language = state["language"]
    copy = COPY[language]
    stage = state["stage"]
    scenario = state.get("scenario", False)
    prompts = BROWSER_COPY[language] if scenario else copy
    if stage in ("completed", "cancelled"):
        return state
    state = {**state, "revision": uuid4().hex}
    if stage not in ("confirm", "select"):
        state["reply"] = copy[stage]

    if stage == "check_in":
        if type(parsed) is date and parsed >= timezone.localdate():
            state.update(stage="check_out", check_in=parsed.isoformat(), reply=copy["ask_check_out"])
    elif stage == "check_out":
        if type(parsed) is date and parsed > date.fromisoformat(state["check_in"]):
            check_out = parsed.isoformat()
            reply = prompts["ask_guests"].format(
                check_in=_display_date(state["check_in"], language),
                check_out=_display_date(check_out, language),
            )
            state.update(stage="guests", check_out=check_out, reply=reply)
    elif stage == "guests":
        if type(parsed) is int and 1 <= parsed <= 32767:
            return _find_room(state, parsed)
    elif stage == "select":
        return _select_room(state, message)
    elif stage == "name":
        if 1 <= len(message) <= 150 and message.isprintable():
            state.update(stage="email", name=message, reply=prompts["ask_email"])
    elif stage == "email":
        try:
            if len(message) > 254 or not message.isprintable():
                raise ValidationError("Invalid email")
            validate_email(message)
        except ValidationError:
            return state
        state["email"] = message
        if scenario:
            return _book(state)
        state.update(stage="confirm", reply=_quote(state))
    elif stage == "confirm":
        return _confirm(state, message)
    return state


def _quote(state):
    language = state["language"]
    return COPY[language]["quote"].format(
        room=state["room"], guests=state["guests"],
        check_in=_display_date(state["check_in"], language),
        check_out=_display_date(state["check_out"], language),
        rate=_price(Decimal(state["nightly_rate"]), language),
        total=_price(Decimal(state["total_price"]), language),
    )


def _find_room(state, guests):
    language = state["language"]
    try:
        offers = available_rooms(
            check_in=date.fromisoformat(state["check_in"]),
            check_out=date.fromisoformat(state["check_out"]),
            guests=guests,
        )
    except BookingError:
        return new_conversation(language, "offer_changed", scenario=state.get("scenario", False))
    if not offers:
        return new_conversation(language, "unavailable", scenario=state.get("scenario", False))
    if state.get("scenario"):
        prompts = BROWSER_COPY[language]
        choices = [
            {"room_id": offer.room_id, "room": offer.room_code,
             "nightly_rate": str(offer.nightly_rate), "total_price": str(offer.total_price)}
            for offer in offers[:2]
        ]
        spoken = " ".join(
            prompts["choice"].format(number=number, room=offer.room_code, total=_price(offer.total_price, language))
            for number, offer in enumerate(offers[:2], start=1)
        )
        state.update(stage="select", guests=guests, choices=choices, reply=prompts["select"].format(options=spoken))
        return state
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


def _select_room(state, message):
    answer = message.casefold().rstrip(".!").strip()
    if state["language"] == "en":
        labels = (("one", "first"), ("two", "second"))
        prefix = "option"
    else:
        labels = (("jeden", "pierwsza"), ("dwa", "druga"))
        prefix = "opcja"
    for number, choice in enumerate(state["choices"], start=1):
        choices = (str(number), *labels[number - 1])
        if answer == choice["room"].casefold() or answer.removeprefix(f"{prefix} ") in choices:
            state.update(choice, stage="confirm", booking_key=str(uuid4()))
            state.pop("choices")
            state["reply"] = _quote(state)
            return state
    return state


def _confirm(state, message):
    language = state["language"]
    yes, no = CONFIRMATIONS[language]
    answer = message.casefold().rstrip(".!").strip()
    if state.get("scenario"):
        accept, decline = ("confirm", "cancel") if language == "en" else ("potwierdzam", "rezygnuję")
        if answer in (no, decline):
            return {
                "language": language, "stage": "cancelled", "revision": state["revision"],
                "reply": BROWSER_COPY[language]["goodbye"], "scenario": True,
            }
        if answer in (yes, accept):
            state.update(stage="name", reply=BROWSER_COPY[language]["ask_name"])
        return state
    if answer == no:
        return new_conversation(language, "cancelled")
    if answer != yes:
        return state
    return _book(state)


def _book(state):
    language = state["language"]
    scenario = state.get("scenario", False)
    try:
        booking, _ = confirm_booking(
            room_id=state["room_id"], check_in=date.fromisoformat(state["check_in"]),
            check_out=date.fromisoformat(state["check_out"]), guests=state["guests"],
            name=state["name"], email=state["email"], nightly_rate=Decimal(state["nightly_rate"]),
            idempotency_key=UUID(state["booking_key"]),
        )
    except BookingError:
        return new_conversation(language, "offer_changed", scenario=scenario)
    reference = str(booking.reference)
    return {
        "language": language, "stage": "completed", "revision": state["revision"], "reference": reference,
        "reply": (BROWSER_COPY if scenario else COPY)[language]["confirmed"].format(reference=reference),
    }
