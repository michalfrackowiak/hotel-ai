from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from django.utils import timezone

from bookings.services import BookingError, available_rooms, confirm_booking

from . import legacy_conversation
from .contacts import parse_email, parse_phone
from .parsing import NUMBER_WORDS, ORDINALS


TERMINAL_STAGES = ("completed", "cancelled", "handover")
ENGLISH_SELECTIONS = {"2", "two", "dwa", "english", "angielski", "po angielsku"}
COPY = {
    "en": {
        "welcome": "Welcome to Resort Neptun Jastrzębia Góra.",
        "check_in": "To check availability, prices, or make a reservation, please give your planned arrival date. For anything else, ask for reception.",
        "check_out": "Please give your departure date or the number of nights you plan to stay. For example, say for three nights.",
        "check_out_retry": "Please give a departure date after arrival or the length of your stay. For example, say for three nights.",
        "guests": "Check-in is {check_in} and checkout is {check_out}. How many guests, including adults and children, will stay?",
        "guests_retry": "Please give the total number of adults and children as a positive whole number.",
        "rooms": "I checked availability. {options} Which option would you like? Give the option number or say room followed by its code.",
        "option": "Option {number}, room {room}, for {total} PLN total.",
        "confirm": "You selected room {room} from {check_in} to {check_out} for {guests} guests. The total is {total} PLN. Would you like to confirm your reservation?",
        "delivery": "How would you like to receive your booking confirmation, by email or by SMS?",
        "email": "Please dictate or type your email address. When dictating, say at and dot.",
        "phone": "Please give the phone number where we should send the SMS confirmation.",
        "invalid_email": "Please give a valid email address.",
        "invalid_phone": "Please give a Polish mobile number or include the international country code.",
        "confirmed_email": "Your reservation is confirmed. Your reference is {reference}. We have requested confirmation by email. Thank you for choosing Resort Neptun!",
        "confirmed_sms": "Your reservation is confirmed. Your reference is {reference}. We have requested confirmation by SMS. Thank you for choosing Resort Neptun!",
        "cancelled": "Thank you for contacting Resort Neptun. Goodbye!",
        "handover": "I’m forwarding your request to reception.",
        "unavailable": "No room is available for that stay. What arrival date would you like to try?",
        "offer_changed": "That room or rate has changed. Please give your arrival date for a fresh quote.",
    },
    "pl": {
        "welcome": "Dzień dobry, Resort Neptun Jastrzębia Góra. For English, choose English or say English.",
        "check_in": "Aby sprawdzić dostępność, ceny lub dokonać rezerwacji, podaj planowaną datę przyjazdu. W innej sprawie poproś o kontakt z recepcją.",
        "check_out": "Podaj datę wyjazdu lub informację, na ile nocy planujesz pobyt. Możesz powiedzieć na przykład na trzy noce.",
        "check_out_retry": "Podaj datę wyjazdu po dacie przyjazdu lub długość pobytu, na przykład na trzy noce.",
        "guests": "Przyjazd {check_in}, wyjazd {check_out}. Podaj łączną liczbę osób, dorosłych i dzieci.",
        "guests_retry": "Podaj łączną liczbę dorosłych i dzieci jako dodatnią liczbę całkowitą.",
        "rooms": "Sprawdziłam dostępność. {options} Którą opcję wybierasz? Podaj numer opcji lub powiedz pokój i jego kod.",
        "option": "Opcja {number}, pokój {room}, za {total} PLN za pobyt.",
        "confirm": "Wybrano pokój {room} w terminie od {check_in} do {check_out} dla {guests} osób. Cena za pobyt to {total} PLN. Czy chcesz potwierdzić rezerwację?",
        "delivery": "Jak chcesz otrzymać potwierdzenie rezerwacji, e-mailem czy SMS-em?",
        "email": "Podyktuj lub wpisz swój adres e-mail. Podczas dyktowania wypowiedz małpa i kropka.",
        "phone": "Podaj numer telefonu, na który mamy wysłać potwierdzenie SMS-em.",
        "invalid_email": "Podaj poprawny adres e-mail.",
        "invalid_phone": "Podaj polski numer komórkowy lub numer z międzynarodowym kodem kraju.",
        "confirmed_email": "Twoja rezerwacja jest potwierdzona. Numer rezerwacji to {reference}. Zleciliśmy wysłanie potwierdzenia e-mailem. Dziękujemy za wybór Resortu Neptun!",
        "confirmed_sms": "Twoja rezerwacja jest potwierdzona. Numer rezerwacji to {reference}. Zleciliśmy wysłanie potwierdzenia SMS-em. Dziękujemy za wybór Resortu Neptun!",
        "cancelled": "Dziękuję za rozmowę i zapraszam do ponownego kontaktu z Resortem Neptun. Do widzenia!",
        "handover": "Przekazuję Twoją sprawę do recepcji.",
        "unavailable": "Brak dostępnych pokoi na ten pobyt. Jaką datę przyjazdu chcesz sprawdzić?",
        "offer_changed": "Dostępność pokoju lub cena uległa zmianie. Podaj datę przyjazdu, aby otrzymać nową ofertę.",
    },
}
CONFIRM = {"en": {"yes", "confirm"}, "pl": {"tak", "potwierdzam"}}
CANCEL = {"en": {"no", "cancel"}, "pl": {"nie", "rezygnuję", "rezygnuje"}}
EMAIL_ANSWERS = {"email", "e-mail", "mail", "e-mailem", "mailem"}
SMS_ANSWERS = {
    "phone", "phone number", "sms", "s m s", "es em es", "text", "text message",
    "telefon", "numer", "numer telefonu", "sms-em",
}


def new_conversation(language, prompt="welcome"):
    copy = COPY[language]
    reply = f"{copy['welcome']} {copy['check_in']}" if prompt == "welcome" else copy[prompt]
    return {"language": language, "stage": "check_in", "revision": uuid4().hex, "reply": reply, "flow": "neptun"}


def _terminal(state, stage, reply, **fields):
    return {
        "language": state["language"], "stage": stage, "revision": state["revision"],
        "reply": reply, "flow": "neptun", **fields,
    }


def _stay_dates(state):
    return tuple(date.fromisoformat(state[key]) for key in ("check_in", "check_out"))


def _display_dates(state):
    return {
        key: legacy_conversation._display_date(state[key], state["language"])
        for key in ("check_in", "check_out")
    }


def _answer(message):
    return message.casefold().strip().rstrip(".!?。！？").strip()


def selects_english(message):
    return _answer(message) in ENGLISH_SELECTIONS


def advance(state, message, parsed, *, voice=False):
    stage = state["stage"]
    if stage in TERMINAL_STAGES:
        return state
    language = state["language"]
    answer = _answer(message)
    if stage == "check_in" and language == "pl" and answer in ENGLISH_SELECTIONS:
        return new_conversation("en", "check_in")
    if state.get("flow") != "neptun":
        if stage != "check_in":
            return legacy_conversation.advance(state, message, parsed)
        state = new_conversation(language, "check_in")

    state = {**state, "revision": uuid4().hex}
    copy = COPY[language]
    if stage == "check_in":
        if type(parsed) is date:
            if parsed >= timezone.localdate():
                state.update(stage="check_out", check_in=parsed.isoformat(), reply=copy["check_out"])
            else:
                state["reply"] = copy["check_in"]
        else:
            return _terminal(state, "handover", copy["handover"])
    elif stage == "check_out":
        if type(parsed) is date and parsed > date.fromisoformat(state["check_in"]):
            state.update(stage="guests", check_out=parsed.isoformat())
            state["reply"] = copy["guests"].format(**_display_dates(state))
        else:
            state["reply"] = copy["check_out_retry"]
    elif stage == "guests":
        if type(parsed) is int and 1 <= parsed <= 32767:
            return _offer_rooms(state, parsed, voice)
        state["reply"] = copy["guests_retry"]
    elif stage == "room":
        return _select_room(state, answer)
    elif stage == "confirm":
        if answer in CANCEL[language]:
            return _terminal(state, "cancelled", copy["cancelled"])
        if answer in CONFIRM[language]:
            state.update(stage="delivery", reply=copy["delivery"])
    elif stage == "delivery":
        if answer in EMAIL_ANSWERS:
            state.update(stage="email", reply=copy["email"])
        elif answer in SMS_ANSWERS:
            state.update(stage="phone", reply=copy["phone"])
    elif stage == "email":
        email = parse_email(message)
        if email is not None:
            return _book(state, email=email, phone="", method="email")
        state["reply"] = copy["invalid_email"]
    elif stage == "phone":
        phone = parse_phone(message)
        if phone is not None:
            return _book(state, email="", phone=phone, method="sms")
        state["reply"] = copy["invalid_phone"]
    return state


def _offer_rooms(state, guests, voice):
    language = state["language"]
    check_in, check_out = _stay_dates(state)
    try:
        offers = available_rooms(check_in=check_in, check_out=check_out, guests=guests)
    except BookingError:
        return new_conversation(language, "offer_changed")
    if not offers:
        return new_conversation(language, "unavailable")
    if voice:
        offers = offers[:2]
    copy = COPY[language]
    options = [
        copy["option"].format(
            number=number, room=offer.room_code, total=legacy_conversation._price(offer.total_price, language),
        )
        for number, offer in enumerate(offers, 1)
    ]
    state.update(
        stage="room", guests=guests, reply=copy["rooms"].format(options=" ".join(options)),
        choices=[
            {"room_id": offer.room_id, "room": offer.room_code,
             "nightly_rate": str(offer.nightly_rate), "total_price": str(offer.total_price)}
            for offer in offers
        ],
    )
    return state


def _select_room(state, answer):
    language = state["language"]
    room_prefix = "room " if language == "en" else "pokój "
    label = answer.removeprefix("option " if language == "en" else "opcja ")
    label = label.removeprefix("number " if language == "en" else "numer ")
    feminine = {"pierwsza": 1, "pierwszą": 1, "druga": 2, "drugą": 2}
    number = int(label) if label.isascii() and label.isdecimal() else (
        NUMBER_WORDS[language].get(label) or ORDINALS[language].get(label)
        or (feminine.get(label) if language == "pl" else None)
    )
    choices = state["choices"]
    if number is not None:
        choice = choices[number - 1] if 1 <= number <= len(choices) else None
    else:
        room_code = answer.removeprefix(room_prefix)
        choice = next((offer for offer in choices if offer["room"].casefold() == room_code), None)
    if choice is None:
        return state
    state.update(choice, stage="confirm", booking_key=str(uuid4()))
    state.pop("choices")
    state["reply"] = COPY[language]["confirm"].format(
        room=state["room"], guests=state["guests"],
        total=legacy_conversation._price(Decimal(state["total_price"]), language), **_display_dates(state),
    )
    return state


def _book(state, *, email, phone, method):
    language = state["language"]
    check_in, check_out = _stay_dates(state)
    try:
        booking, _ = confirm_booking(
            room_id=state["room_id"], check_in=check_in, check_out=check_out, guests=state["guests"],
            name="", email=email, phone=phone, delivery_method=method, language=language,
            nightly_rate=Decimal(state["nightly_rate"]), idempotency_key=UUID(state["booking_key"]),
        )
    except BookingError as error:
        if error.code in {"invalid_email", "invalid_phone"}:
            state["reply"] = COPY[language][error.code]
            return state
        return new_conversation(language, "offer_changed")
    reference = str(booking.reference)
    reply = COPY[language][f"confirmed_{method}"].format(reference=reference)
    return _terminal(state, "completed", reply, reference=reference)
