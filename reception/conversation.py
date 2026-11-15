from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from django.utils import timezone

from bookings.services import BookingError, available_rooms, confirm_booking

from . import legacy_conversation
from .contacts import parse_email, parse_phone
from .parsing import NUMBER_WORDS, ORDINALS, StayDates, parse_stay_input
from .receipt import DELIVERY_PENDING, start_receipt


TERMINAL_STAGES = ("completed", "cancelled", "handover")
ENGLISH_SELECTIONS = {"2", "two", "dwa", "english", "english please", "angielski", "po angielsku"}
WELCOME_PARTS = (
    ("pl", "Dzień dobry, Resort Neptun Jastrzębia Góra."),
    ("en", "For English press 2 or say 'English'."),
    ("pl", "Język polski – pozostań na linii lub napisz wiadomość."),
)
WELCOME = " ".join(text for _, text in WELCOME_PARTS)
COPY = {
    "en": {
        "welcome": WELCOME,
        "check_in": "If you would like to check availability, prices, or make a reservation, please state your planned arrival date. If you are calling regarding another matter, please stay on the line and I will connect you with a receptionist.",
        "check_out": "Please enter your departure date or the number of nights for your stay.",
        "check_out_retry": "Please give a departure date after arrival or the length of your stay. For example, say for three nights.",
        "guests": "Please tell me the total number of guests (adults and children) for the reservation.",
        "stay_dates": "Check-in is {check_in} and check-out is {check_out}.",
        "guests_retry": "Please give the total number of adults and children as a positive whole number.",
        "rooms": "I have checked availability. For your dates, we have the following rooms available: {options}. Which option would you like to select?",
        "option": "room {room} for {total} PLN total",
        "voice_option": "option {number}, room {room} for {total} PLN total",
        "and": "and",
        "confirm": "You have selected room {room} from {check_in} to {check_out} for {guests} guests. Would you like to confirm your reservation?",
        "delivery": "Your reservation has been tentatively created! Would you like your booking confirmation sent to your email address or via SMS to your phone number?",
        "email": "Please dictate or type your email address.",
        "phone": "Please provide the phone number where we should send the SMS confirmation.",
        "invalid_email": "Please give a valid email address. If you are dictating it, spell it and say at and dot.",
        "invalid_phone": "Please give a Polish mobile number or include the international country code. If you are speaking, say the digits one at a time.",
        "cancelled": "Thank you for your call, and feel free to contact Neptun Resort again. Goodbye!",
        "handover": "Redirecting you to a reception employee. Please hold.",
        "unavailable": "No room is available for that stay. What arrival date would you like to try?",
        "offer_changed": "That room or rate has changed. Please give your arrival date for a fresh quote.",
    },
    "pl": {
        "welcome": WELCOME,
        "check_in": "Jeżeli chcesz dowiedzieć się o dostępność, ceny lub dokonać rezerwacji, proszę podaj planowany termin przyjazdu. Jeżeli dzwonisz w innej sprawie, pozostań na linii lub napisz, a połączę Cię z pracownikiem recepcji.",
        "check_out": "Podaj datę wyjazdu lub informację, na ile nocy planujesz pobyt.",
        "check_out_retry": "Podaj datę wyjazdu po dacie przyjazdu lub długość pobytu, na przykład na trzy noce.",
        "guests": "Podaj łączną liczbę osób (dorosłych i dzieci), dla których mamy przygotować rezerwację.",
        "stay_dates": "Przyjazd {check_in}, wyjazd {check_out}.",
        "guests_retry": "Podaj łączną liczbę dorosłych i dzieci jako dodatnią liczbę całkowitą.",
        "rooms": "Sprawdziłam dostępność. Na wybrany termin mamy następujące wolne pokoje: {options}. Którą opcję chciałbyś wybrać?",
        "option": "pokój {room} w cenie {total} PLN za pobyt",
        "voice_option": "opcja {number}, pokój {room} w cenie {total} PLN za pobyt",
        "and": "oraz",
        "confirm": "Wybrałeś pokój {room} w terminie od {check_in} do {check_out} dla {guests} osób. Czy chcesz potwierdzić rezerwację?",
        "delivery": "Rezerwacja została wstępnie utworzona! Czy chcesz, aby potwierdzenie rezerwacji zostało wysłane na adres e-mail, czy na numer telefonu przez SMS?",
        "email": "Proszę, podyktuj lub wpisz swój adres e-mail.",
        "phone": "Proszę, podaj numer telefonu, na który mamy wysłać potwierdzenie SMS-em.",
        "invalid_email": "Podaj poprawny adres e-mail. Jeśli dyktujesz, przeliteruj go i powiedz małpa oraz kropka.",
        "invalid_phone": "Podaj polski numer komórkowy lub numer z międzynarodowym kodem kraju. Jeśli dyktujesz, podawaj cyfry pojedynczo.",
        "cancelled": "Dziękuję za rozmowę i zapraszam do ponownego kontaktu z Resortem Neptun. Do widzenia!",
        "handover": "Przekierowuję do pracownika recepcji. Proszę czekać.",
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
    return {
        "language": language, "stage": "welcome" if prompt == "welcome" else "check_in",
        "revision": uuid4().hex, "reply": COPY[language][prompt], "flow": "neptun",
    }


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
    if stage in TERMINAL_STAGES or stage == DELIVERY_PENDING:
        return state
    language = state["language"]
    answer = _answer(message)
    if stage in ("welcome", "check_in") and language == "pl" and answer in ENGLISH_SELECTIONS:
        return new_conversation("en", "check_in")
    if stage == "welcome":
        state = new_conversation(language, "check_in")
        if not isinstance(parsed, (date, StayDates)):
            return state
        stage = "check_in"
    if state.get("flow") != "neptun":
        if stage != "check_in":
            return legacy_conversation.advance(state, message, parsed)
        state = new_conversation(language, "check_in")

    state = {**state, "revision": uuid4().hex}
    copy = COPY[language]
    if stage == "check_in":
        if isinstance(parsed, StayDates):
            if timezone.localdate() <= parsed.check_in < parsed.check_out:
                state.update(stage="guests", check_in=parsed.check_in.isoformat(), check_out=parsed.check_out.isoformat())
                state["reply"] = f"{copy['stay_dates'].format(**_display_dates(state))} {copy['guests']}"
            else:
                state["reply"] = copy["check_in"]
        elif type(parsed) is date:
            if parsed >= timezone.localdate():
                state.update(stage="check_out", check_in=parsed.isoformat(), reply=copy["check_out"])
            else:
                state["reply"] = copy["check_in"]
        else:
            return _terminal(state, "handover", copy["handover"])
    elif stage == "check_out":
        if type(parsed) is date and parsed > date.fromisoformat(state["check_in"]):
            state.update(stage="guests", check_out=parsed.isoformat(), reply=copy["guests"])
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
            state.setdefault("booking_key", str(uuid4()))
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
    option = copy["voice_option" if voice else "option"]
    options = [
        option.format(
            number=number, room=offer.room_code, total=legacy_conversation._price(offer.total_price, language),
        )
        for number, offer in enumerate(offers, 1)
    ]
    room_list = options[0] if len(options) == 1 else f"{', '.join(options[:-1])}, {copy['and']} {options[-1]}"
    state.update(
        stage="room", guests=guests, reply=copy["rooms"].format(options=room_list),
        choices=[
            {"room_id": offer.room_id, "room": offer.room_code,
             "nightly_rate": str(offer.nightly_rate), "total_price": str(offer.total_price)}
            for offer in offers
        ],
    )
    return state


def _select_room(state, answer):
    choice = _room_choice(state, answer)
    if choice is None:
        return state
    state.update(choice, stage="confirm")
    state.pop("choices")
    language = state["language"]
    state["reply"] = COPY[language]["confirm"].format(
        room=state["room"], guests=state["guests"], **_display_dates(state),
    )
    return state


def _room_choice(state, answer):
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
        return choices[number - 1] if 1 <= number <= len(choices) else None
    room_code = answer.removeprefix(room_prefix)
    return next((offer for offer in choices if offer["room"].casefold() == room_code), None)


def recognizes_booking_answer(state, message):
    """Check whether a transcript fits the current prompt without advancing a booking."""
    stage, language = state["stage"], state["language"]
    answer = _answer(message)
    check_in = date.fromisoformat(state["check_in"]) if stage == "check_out" else None
    parser_stage = "check_in" if stage == "welcome" else stage
    parsed = parse_stay_input(parser_stage, message, language, check_in=check_in)
    if stage in ("welcome", "check_in"):
        if language == "pl" and selects_english(message):
            return True
        if isinstance(parsed, StayDates):
            return timezone.localdate() <= parsed.check_in < parsed.check_out
        return type(parsed) is date and parsed >= timezone.localdate()
    if stage == "check_out":
        return type(parsed) is date and parsed > check_in
    if stage == "guests":
        return type(parsed) is int and 1 <= parsed <= 32767
    if stage == "room":
        return _room_choice(state, answer) is not None
    if stage == "confirm":
        return answer in CONFIRM[language] or answer in CANCEL[language]
    if stage == "delivery":
        return answer in EMAIL_ANSWERS or answer in SMS_ANSWERS
    return False


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
    return start_receipt(state, str(booking.reference), method)
