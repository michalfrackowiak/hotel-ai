from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from django.utils import timezone

from bookings.services import BookingError, available_rooms, confirm_booking

from . import legacy_conversation
from .contacts import parse_email, parse_phone
from .parsing import NUMBER_WORDS, ORDINALS, StayDates, parse_stay_input
from .receipt import DELIVERY_PENDING, start_receipt, waits_for_confirmation
from .scenario import room_options, set_prompt


TERMINAL_STAGES = ("completed", "cancelled", "handover")
ENGLISH_SELECTIONS = {"2", "two", "dwa", "english", "english please", "angielski", "po angielsku"}
WELCOME_PARTS = (
    ("pl", "Dzień dobry, Resort Neptun Jastrzębia Góra."),
    ("en", "For English press 2 or say 'English'."),
    ("pl", "Język polski – pozostań na linii lub napisz wiadomość."),
)
WELCOME = " ".join(text for _, text in WELCOME_PARTS)
ARRIVAL_PARTS = {
    "en": (
        {"text": "Hi, this is Neptun Resort, Pucka 2 street", "seconds": 2, "listen": False},
        {"text": "For availability, prices, booking. Specify the dates of your stay", "seconds": 5, "listen": True},
        {
            "text": "If you are calling regarding another matter, please stay on the line and I will connect you with a receptionist. "
                    "For availability, prices, booking - Specify the dates of your stay",
            "seconds": 5,
            "listen": True,
        },
    ),
    "pl": (
        {"text": "Dostępność, ceny, rezerwacje. Proszę podaj planowany termin przyjazdu.", "seconds": 5, "listen": True},
        {
            "text": "Jeżeli dzwonisz w innej sprawie, pozostań na linii. "
                    "Dostępność, ceny, rezerwacje. Proszę podaj planowany termin przyjazdu.",
            "seconds": 5,
            "listen": True,
        },
    ),
}
COPY = {
    "en": {
        "welcome": WELCOME,
        "check_in": "If you would like to check availability, prices, or make a reservation, please state your planned arrival date. If you are calling regarding another matter, please stay on the line and I will connect you with a receptionist.",
        "check_out": "Please enter your departure date or the number of nights for your stay.",
        "check_out_retry": "Please give a departure date after arrival or the length of your stay. For example, say for three nights.",
        "guests": "Please tell me the total number of guests (adults and children) for the reservation.",
        "stay_dates": "Check-in is {check_in} and check-out is {check_out}.",
        "stay_dates_required": "Please give both your arrival and departure dates in one answer.",
        "guests_retry": "Please give the total number of adults and children as a positive whole number.",
        "rooms": "I have checked availability. For your dates, we have the following rooms available: {options}. Which option would you like to select?",
        "option": "room {room} for {total} PLN total",
        "voice_option": "option {number}, room {room} for {total} PLN total",
        "and": "and",
        "confirm": "You have selected room {room} from {check_in} to {check_out} for {guests} guests. Would you like to confirm your reservation?",
        "delivery": "Your reservation has been tentatively created! Would you like your booking confirmation sent to your email address or via SMS to your phone number?",
        "email": "Please give your email address.",
        "phone": "Please provide the phone number where we should send the SMS confirmation.",
        "confirm_email": "The email address is {contact}. Is this correct? Say yes to confirm or no to change it.",
        "confirm_phone": "The phone number is {contact}. Is this correct? Say yes to confirm or no to change it.",
        "invalid_email": "Please give a valid email address. If you are dictating it, spell it and say at and dot. For help, say reception.",
        "invalid_phone": "Please give a Polish mobile number or include the international country code. If you are speaking, say the digits one at a time. For help, say reception.",
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
        "stay_dates_required": "Podaj datę przyjazdu i datę wyjazdu w jednej odpowiedzi.",
        "guests_retry": "Podaj łączną liczbę dorosłych i dzieci jako dodatnią liczbę całkowitą.",
        "rooms": "Sprawdziłam dostępność. Na wybrany termin mamy następujące wolne pokoje: {options}. Którą opcję chciałbyś wybrać?",
        "option": "pokój {room} w cenie {total} PLN za pobyt",
        "voice_option": "opcja {number}, pokój {room} w cenie {total} PLN za pobyt",
        "and": "oraz",
        "confirm": "Wybrałeś pokój {room} w terminie od {check_in} do {check_out} dla {guests} osób. Czy chcesz potwierdzić rezerwację?",
        "delivery": "Rezerwacja została wstępnie utworzona! Czy chcesz, aby potwierdzenie rezerwacji zostało wysłane na adres e-mail, czy na numer telefonu przez SMS?",
        "email": "Proszę podaj swój adres e-mail.",
        "phone": "Proszę, podaj numer telefonu, na który mamy wysłać potwierdzenie SMS-em.",
        "confirm_email": "Podany adres e-mail to {contact}. Czy to się zgadza? Powiedz tak, aby potwierdzić, lub nie, aby zmienić.",
        "confirm_phone": "Podany numer telefonu to {contact}. Czy to się zgadza? Powiedz tak, aby potwierdzić, lub nie, aby zmienić.",
        "invalid_email": "Podaj poprawny adres e-mail. Jeśli dyktujesz, przeliteruj go i powiedz małpa oraz kropka. Aby uzyskać pomoc, powiedz recepcja.",
        "invalid_phone": "Podaj polski numer komórkowy lub numer z międzynarodowym kodem kraju. Jeśli dyktujesz, podawaj cyfry pojedynczo. Aby uzyskać pomoc, powiedz recepcja.",
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
CONTACT_STAGES = ("email", "phone", "contact_confirm")
CONTACT_CHANGES = {
    "en": {"change", "change contact"},
    "pl": {"zmień", "zmien", "zmień dane", "zmien dane"},
}
DELIVERY_CHANGES = {
    "en": {"change delivery", "change delivery method"},
    "pl": {"zmień sposób dostawy", "zmien sposob dostawy", "zmień sposób potwierdzenia"},
}
CONTACT_HANDOVER = {
    "en": {"reception", "receptionist", "speak to reception"},
    "pl": {"recepcja", "recepcjonista", "połącz z recepcją"},
}


def new_conversation(language, prompt="welcome"):
    return set_prompt({
        "language": language, "stage": "welcome" if prompt == "welcome" else "check_in",
        "revision": uuid4().hex, "flow": "neptun",
    }, prompt)


def _terminal(state, stage):
    return set_prompt({
        "language": state["language"], "stage": stage, "revision": state["revision"],
        "flow": "neptun",
    }, stage)


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


def _allows_transition(state, event, destination, resolve_transition):
    return resolve_transition is None or resolve_transition(state, event) == destination


def advance(
    state, message, parsed, *, voice=False, input_parsed=False, resolve_transition=None, timeout=False,
    require_arrival_range=False, room_search=None,
):
    stage = state["stage"]
    if stage in TERMINAL_STAGES or stage == DELIVERY_PENDING:
        return state
    language = state["language"]
    answer = _answer(message)
    if stage in ("welcome", "check_in") and language == "pl" and answer in ENGLISH_SELECTIONS:
        return new_conversation("en", "check_in")
    today = timezone.localdate() if stage in ("welcome", "check_in") else None
    if stage == "welcome":
        if resolve_transition is not None:
            if isinstance(parsed, StayDates) and today <= parsed.check_in < parsed.check_out:
                event, destination = "stay_dates", "guests"
            elif type(parsed) is date and parsed >= today and not require_arrival_range:
                event, destination = "arrival", "check_out"
            else:
                event, destination = ("timeout" if timeout else "continue"), "check_in"
            if not _allows_transition(state, event, destination, resolve_transition):
                return {**state, "revision": uuid4().hex}
            # The validated welcome edge covers the same-turn date handling.
            resolve_transition = None
        state = new_conversation(language, "check_in")
        if not isinstance(parsed, (date, StayDates)):
            return state
        stage = "check_in"
    if state.get("flow") != "neptun":
        # Old drafts could reach the stay quote with an unread email already saved.
        if (
            stage == "confirm" and answer == legacy_conversation.CONFIRMATIONS[language][0]
            and not state.get("scenario") and not state.get("legacy_contact_confirmed")
        ):
            state = {**state, "revision": uuid4().hex, "legacy_quote_approved": True}
            candidate = parse_email(state.pop("email", ""))
            return _set_candidate(state, "email", candidate) if candidate else _contact_entry(state, "email")
        if stage not in (*CONTACT_STAGES, "check_in"):
            return legacy_conversation.advance(state, message, parsed)
        if stage == "check_in":
            state = new_conversation(language, "check_in")

    state = {**state, "revision": uuid4().hex}
    if stage == "check_in":
        retry_prompt = "stay_dates_required" if require_arrival_range else "check_in"
        if isinstance(parsed, StayDates):
            valid = today <= parsed.check_in < parsed.check_out
            event, destination = ("stay_dates", "guests") if valid else ("retry", "check_in")
            allowed = _allows_transition(state, event, destination, resolve_transition)
            if valid and allowed:
                state.update(stage="guests", check_in=parsed.check_in.isoformat(), check_out=parsed.check_out.isoformat())
                set_prompt(state, "stay_dates", _display_dates(state), tail="guests")
            else:
                set_prompt(state, retry_prompt)
        elif type(parsed) is date:
            valid = parsed >= today and not require_arrival_range
            event, destination = ("arrival", "check_out") if valid else ("retry", "check_in")
            allowed = _allows_transition(state, event, destination, resolve_transition)
            if valid and allowed:
                state.update(stage="check_out", check_in=parsed.isoformat())
                set_prompt(state, "check_out")
            else:
                set_prompt(state, retry_prompt)
        else:
            event = "timeout" if timeout else "unrecognized"
            if _allows_transition(state, event, "handover", resolve_transition):
                return _terminal(state, "handover")
            set_prompt(state, "check_in")
    elif stage == "check_out":
        departure = type(parsed) is date and parsed > date.fromisoformat(state["check_in"])
        event, destination = ("departure", "guests") if departure else ("retry", "check_out")
        allowed = _allows_transition(state, event, destination, resolve_transition)
        if departure and allowed:
            state.update(stage="guests", check_out=parsed.isoformat())
            set_prompt(state, "guests")
        else:
            set_prompt(state, "check_out_retry")
    elif stage == "guests":
        if type(parsed) is int and 1 <= parsed <= 32767:
            return _offer_rooms(state, parsed, voice, resolve_transition, room_search)
        _allows_transition(state, "retry", "guests", resolve_transition)
        set_prompt(state, "guests_retry")
    elif stage == "room":
        choice = parsed if input_parsed else _room_choice(state, answer)
        event, destination = ("selected", "confirm") if choice is not None else ("retry", "room")
        if _allows_transition(state, event, destination, resolve_transition):
            return _select_room(state, choice)
    elif stage == "confirm":
        if answer in CANCEL[language]:
            if _allows_transition(state, "decline_quote", "cancelled", resolve_transition):
                return _terminal(state, "cancelled")
        elif answer in CONFIRM[language]:
            if not _allows_transition(state, "approve_quote", "delivery", resolve_transition):
                return state
            state["stage"] = "delivery"
            set_prompt(state, "delivery")
            state.setdefault("booking_key", str(uuid4()))
        else:
            _allows_transition(state, "retry", "confirm", resolve_transition)
    elif stage == "delivery":
        if answer in EMAIL_ANSWERS:
            event, destination = "email", "email"
        elif answer in SMS_ANSWERS:
            event, destination = "sms", "phone"
        else:
            event, destination = "retry", "delivery"
        if _allows_transition(state, event, destination, resolve_transition) and destination != "delivery":
            state["stage"] = destination
            set_prompt(state, destination)
    elif stage in CONTACT_STAGES:
        return _contact_turn(state, message, answer, parsed if input_parsed else None, resolve_transition)
    return state


def _clear_contact(state):
    state.pop("contact_candidate", None)
    state.pop("contact_method", None)


def _contact_entry(state, stage):
    _clear_contact(state)
    state["stage"] = stage
    return set_prompt(state, stage)


def _set_candidate(state, method, candidate):
    entry = "email" if method == "email" else "phone"
    state.update(stage="contact_confirm", contact_method=method, contact_candidate=candidate)
    return set_prompt(state, f"confirm_{entry}", {"contact": candidate})


def _contact_turn(state, message, answer, parsed, resolve_transition):
    language = state["language"]
    stage = state["stage"]
    reviewing = stage == "contact_confirm"
    method = state["contact_method"] if reviewing else ("email" if stage == "email" else "sms")
    entry = "email" if method == "email" else "phone"
    if answer in CONTACT_HANDOVER[language]:
        if _allows_transition(state, "reception", "handover", resolve_transition):
            return _terminal(state, "handover")
        return state
    if state.get("flow") == "neptun":
        if answer in DELIVERY_CHANGES[language]:
            return _route_contact_entry(state, "change_delivery", "delivery", resolve_transition)
        if answer in EMAIL_ANSWERS:
            return _route_contact_entry(state, "choose_email", "email", resolve_transition)
        if answer in SMS_ANSWERS:
            return _route_contact_entry(state, "choose_sms", "phone", resolve_transition)
    if reviewing:
        if answer in CANCEL[language] or answer in CONTACT_CHANGES[language]:
            return _route_contact_entry(state, f"edit_{entry}", entry, resolve_transition)
        if answer in CONFIRM[language]:
            candidate = state["contact_candidate"]
            if state.get("flow") != "neptun":
                _clear_contact(state)
                for field in ("prompt_key", "prompt_values", "prompt_tail"):
                    state.pop(field, None)
                if state.get("legacy_quote_approved"):
                    state.update(stage="confirm", email=candidate)
                    yes = legacy_conversation.CONFIRMATIONS[language][0]
                    return legacy_conversation.advance(state, yes, None)
                state.update(stage="email", legacy_contact_confirmed=True)
                return legacy_conversation.advance(state, candidate, None)
            return _book(
                state, email=candidate if method == "email" else "",
                phone=candidate if method == "sms" else "", method=method, resolve_transition=resolve_transition,
            )
    parse_contact = parse_email if method == "email" else parse_phone
    candidate = parsed or parse_contact(message)
    if candidate is None:
        _allows_transition(state, "retry", stage, resolve_transition)
        return state if reviewing else set_prompt(state, f"invalid_{entry}")
    event = "replace_contact" if reviewing else "valid_contact"
    if _allows_transition(state, event, "contact_confirm", resolve_transition):
        return _set_candidate(state, method, candidate)
    return state


def _route_contact_entry(state, event, entry, resolve_transition):
    if _allows_transition(state, event, entry, resolve_transition):
        return _contact_entry(state, entry)
    return state


def _offer_rooms(state, guests, voice, resolve_transition, room_search):
    language = state["language"]
    check_in, check_out = _stay_dates(state)
    search = available_rooms if room_search is None else room_search
    try:
        offers = search(check_in=check_in, check_out=check_out, guests=guests)
    except BookingError:
        if _allows_transition(state, "offer_changed", "check_in", resolve_transition):
            return new_conversation(language, "offer_changed")
        return state
    if not offers:
        if _allows_transition(state, "unavailable", "check_in", resolve_transition):
            return new_conversation(language, "unavailable")
        return state
    if not _allows_transition(state, "available", "room", resolve_transition):
        return state
    if voice:
        offers = offers[:2]
    state.update(
        stage="room", guests=guests,
        choices=[
            {"room_id": offer.room_id, "room": offer.room_code,
             "nightly_rate": str(offer.nightly_rate), "total_price": str(offer.total_price)}
            for offer in offers
        ],
    )
    return set_prompt(state, "rooms", {"options": room_options(state, voice=voice)})


def _select_room(state, choice):
    if choice is None:
        return state
    state.update(choice, stage="confirm")
    state.pop("choices")
    return set_prompt(state, "confirm", {"room": state["room"], "guests": state["guests"], **_display_dates(state)})


def _room_option_number(language, answer):
    label = answer.removeprefix("option " if language == "en" else "opcja ")
    label = label.removeprefix("number " if language == "en" else "numer ")
    feminine = {"pierwsza": 1, "pierwszą": 1, "druga": 2, "drugą": 2}
    return int(label) if label.isascii() and label.isdecimal() else (
        NUMBER_WORDS[language].get(label) or ORDINALS[language].get(label)
        or (feminine.get(label) if language == "pl" else None)
    )


def _room_choice(state, answer):
    language = state["language"]
    room_prefix = "room " if language == "en" else "pokój "
    number = _room_option_number(language, answer)
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


def _book(state, *, email, phone, method, resolve_transition):
    language = state["language"]
    check_in, check_out = _stay_dates(state)
    wait = waits_for_confirmation(method)
    event, destination = (
        ("approve_contact", DELIVERY_PENDING) if wait else ("approve_contact_without_wait", "completed")
    )
    if not _allows_transition(state, event, destination, resolve_transition):
        return state
    try:
        booking, _ = confirm_booking(
            room_id=state["room_id"], check_in=check_in, check_out=check_out, guests=state["guests"],
            name="", email=email, phone=phone, delivery_method=method, language=language,
            nightly_rate=Decimal(state["nightly_rate"]), idempotency_key=UUID(state["booking_key"]),
        )
    except BookingError as error:
        if error.code in {"invalid_email", "invalid_phone"}:
            entry = "email" if method == "email" else "phone"
            if _allows_transition(state, error.code, entry, resolve_transition):
                _contact_entry(state, entry)
                return set_prompt(state, error.code)
        elif _allows_transition(state, "offer_changed", "check_in", resolve_transition):
            return new_conversation(language, "offer_changed")
        return state
    return start_receipt(state, str(booking.reference), method, wait=wait)
