"""In-memory preview of one browser booking reply using built-in parsing and fictional inventory."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from bookings.models import BookingConfirmation
from bookings.services import RoomOffer
from model_providers.extraction import validate_model_stay_value

from .conversation import WELCOME_PARTS, advance, new_conversation, selects_english
from .parser_review import extracted_booking_value, review_text_allowed
from .parser_training import CONTACT_STAGES, apply_reviewed_correction, validate_correction
from .parsing import StayDates, parse_stay_input
from .receipt import DELIVERY_PENDING, apply_receipt_outcome, start_receipt
from .scenario import default_chat_text, render_chat, set_prompt, split_voice_parts, voice_segments
from .scenario_dispatch import RETRY_COUNT, RETRY_HANDOVER, advance_counted_turn
from .scenario_pinned_prompts import browser_prompt_bindings, browser_prompt_overrides
from .scenario_step_settings import (
    ARRIVAL_INPUT_FIELD, CONTINUE_IN_CHAT, MAX_RETRY_LIMIT, PARSER_CORRECTIONS_STEPS, REQUIRE_ARRIVAL_RANGE,
    STAY_PARSER_STEPS,
    allows_model_fallback, allows_parser_corrections, retry_limit_value, silence_window_seconds, voice_timeout_mode,
)
from .scenario_validation import approved_destination
from .speech import MAX_TEXT_CHARACTERS


FICTIONAL_ROOMS = (
    (-1, "Demo Double", 2, Decimal("120")),
    (-2, "Demo Family", 6, Decimal("200")),
)
FICTIONAL_ROOM_CODES = frozenset(name.casefold() for _, name, _, _ in FICTIONAL_ROOMS)
FICTIONAL_CONTACTS = {
    "confirm_email": ("email", "guest@example.com"),
    "confirm_sms": ("sms", "+1 202 555 0142"),
}


RECEIPT_METHODS = {"receipt_email": "email", "receipt_sms": "sms"}
RECEIPT_OUTCOMES = {
    "waiting": (BookingConfirmation.Status.SENDING, False, "Sending before deadline"),
    "accepted": (BookingConfirmation.Status.ACCEPTED, False, "Accepted by provider"),
    "pending": (BookingConfirmation.Status.PENDING, True, "Pending at deadline"),
    "unverified": (BookingConfirmation.Status.SENDING, True, "Sending at deadline, unverified"),
    "failed": (BookingConfirmation.Status.FAILED, False, "Submission failed"),
}
TIMEOUT_STEPS = {
    "arrival_chat_timeout": "check_in",
    "arrival_voice_timeout": "check_in",
    "welcome_timeout": "welcome",
    "welcome_voice_timeout": "welcome",
}
VOICE_TIMEOUT_PREVIEWS = {"arrival_voice_timeout", "welcome_voice_timeout"}
LATER_VOICE_TIMEOUT_CHOICES = (
    ("check_out", "Departure"), ("guests", "Guest count"), ("room", "Room selection"),
    ("confirm", "Quote decision"), ("delivery", "Delivery choice"), ("email", "Email entry"),
    ("phone", "Phone entry"), ("confirm_email", "Contact review (email)"), ("confirm_sms", "Contact review (SMS)"),
)
LATER_VOICE_TIMEOUT_STEPS = {f"{step}_voice_timeout": step for step, _ in LATER_VOICE_TIMEOUT_CHOICES}


class BookingReplySimulationForm(forms.Form):
    stage = forms.ChoiceField(label="Booking step", choices=(
        ("welcome", "Polish welcome reply"),
        ("check_in", "Arrival"), ("check_out", "Departure"), ("guests", "Guest count"),
        ("room", "Room selection"), ("confirm", "Quote decision"),
        ("delivery", "Delivery choice"), ("email", "Email entry"), ("phone", "Phone entry"),
        ("confirm_email", "Contact review (email)"), ("confirm_sms", "Contact review (SMS)"),
        ("receipt_email", "Delivery receipt (email)"), ("receipt_sms", "Delivery receipt (SMS)"),
        ("arrival_chat_timeout", "Arrival timeout (chat)"),
        ("arrival_voice_timeout", "Arrival timeout (final voice listening window)"),
        ("welcome_timeout", "Welcome timeout (chat)"),
        ("welcome_voice_timeout", "Welcome timeout (final voice listening window)"),
        *((f"{step}_voice_timeout", f"{label} timeout (final voice listening window)")
          for step, label in LATER_VOICE_TIMEOUT_CHOICES),
    ))
    reply = forms.CharField(label="Example guest reply", max_length=500, required=False)
    fictional_correction = forms.CharField(
        label="Fictional parser correction", max_length=500, required=False,
        help_text=(
            "Optional. Use fictional dates, guest counts, room options, email addresses at example.com, "
            "example.net or example.org, or phone numbers from +1 202 555 0100 to +1 202 555 0199."
        ),
    )
    fictional_model_value = forms.CharField(
        label="Fictional model result", max_length=10, required=False,
        help_text="Optional. Enter a date as YYYY-MM-DD or a whole-number guest count.",
    )
    prior_retries = forms.IntegerField(
        label="Prior consecutive retries", min_value=0, max_value=MAX_RETRY_LIMIT - 1, required=False,
        help_text="Optional. For a reply step with a positive retry limit, enter a count below that limit.",
    )
    receipt_outcome = forms.ChoiceField(label="Fictional receipt status", required=False, choices=(
        ("", "Choose a status for a receipt preview"),
        *((key, label) for key, (_, _, label) in RECEIPT_OUTCOMES.items()),
    ))

    def __init__(self, *args, language, channel="chat", **kwargs):
        super().__init__(*args, **kwargs)
        self.language = language
        self.channel = channel
        self.retry_limits = {}
        if language != "pl":
            field = self.fields["stage"]
            field.choices = [
                (key, label) for key, label in field.choices if TIMEOUT_STEPS.get(key, key) != "welcome"
            ]

    def clean(self):
        values = super().clean()
        stage = values.get("stage")
        parser_stage = "check_in" if stage == "welcome" else stage
        if stage in RECEIPT_METHODS and values.get("receipt_outcome") == "":
            self.add_error("receipt_outcome", "Choose a fictional receipt status.")
        elif (stage and stage not in RECEIPT_METHODS and stage not in TIMEOUT_STEPS
              and stage not in LATER_VOICE_TIMEOUT_STEPS and values.get("reply") == ""):
            self.add_error("reply", "Enter an example guest reply.")
        if stage and (prior_retries := values.get("prior_retries")):
            retry_step = "contact_confirm" if stage in FICTIONAL_CONTACTS else stage
            limit = self.retry_limits.get(retry_step)
            if not limit:
                self.add_error("prior_retries", "Choose a reply step with a positive retry limit.")
            elif prior_retries >= limit:
                self.add_error("prior_retries", f"Enter a count below this step's retry limit of {limit}.")
        if (corrected := values.get("fictional_correction")) and (reply := values.get("reply")):
            if parser_stage not in PARSER_CORRECTIONS_STEPS:
                self.add_error("fictional_correction", "Choose a date, guest count, room selection, or contact entry reply.")
            elif parser_stage not in CONTACT_STAGES and not (review_text_allowed(reply) and review_text_allowed(corrected)):
                self.add_error("fictional_correction", "Use fictional text without contact details.")
            else:
                try:
                    validated = validate_correction(
                        parser_stage, reply, corrected, language=self.language, source=self.channel,
                        room_codes=FICTIONAL_ROOM_CODES,
                    )
                    if parser_stage == "email" and validated.rsplit("@", 1)[-1].casefold() not in {
                        "example.com", "example.net", "example.org",
                    }:
                        raise ValidationError("Use an example.com, example.net or example.org address.")
                    if parser_stage == "phone" and (len(validated) != 12 or not validated.startswith("+120255501")):
                        raise ValidationError("Use a phone number from +1 202 555 0100 to +1 202 555 0199.")
                    values["fictional_correction"] = validated
                except ValidationError as error:
                    self.add_error("fictional_correction", error)
        elif values.get("fictional_correction"):
            self.add_error("fictional_correction", "Enter an example guest reply for the correction.")
        if model_value := values.get("fictional_model_value"):
            if parser_stage not in STAY_PARSER_STEPS:
                self.add_error("fictional_model_value", "Choose a welcome, arrival, departure, or guest-count reply.")
            elif not values.get("reply") or not review_text_allowed(values["reply"]):
                self.add_error("fictional_model_value", "Enter a fictional guest reply without contact details.")
            else:
                if parser_stage == "guests" and model_value.isascii() and model_value.isdecimal():
                    model_value = int(model_value)
                try:
                    validated = validate_model_stay_value(parser_stage, model_value)
                except ValueError:
                    validated = None
                if validated is None:
                    self.add_error(
                        "fictional_model_value",
                        "Enter a valid YYYY-MM-DD date or a count from 1 to 32767 for the selected step.",
                    )
                else:
                    values["fictional_model_value"] = validated
        return values


def _fictional_room_search(*, check_in, check_out, guests):
    nights = (check_out - check_in).days
    return [
        RoomOffer(room_id, name, capacity, rate, rate * nights)
        for room_id, name, capacity, rate in FICTIONAL_ROOMS
        if guests <= capacity
    ]


def _fictional_booking(**_booking_details):
    return SimpleNamespace(reference="DEMO-REFERENCE"), True


def simulate_booking_reply(
    language, channel, steps, transitions, *, stage, reply, prior_retries=None, receipt_outcome="",
    fictional_correction="", fictional_model_value="",
):
    step_keys = {step.pk: step.key for step, _ in steps}
    edges = {(step_keys[source], event): step_keys[target] for source, event, target in transitions}
    configurations = {step.key: configuration for step, configuration in steps}
    final_voice_timeout = stage in LATER_VOICE_TIMEOUT_STEPS
    if final_voice_timeout:
        stage = LATER_VOICE_TIMEOUT_STEPS[stage]
    fictional_contact = FICTIONAL_CONTACTS.get(stage)
    if fictional_contact:
        stage = "contact_confirm"
    decisions = []

    def resolve(state, event):
        source = state["stage"]
        target = edges.get((source, event))
        allowed = target is not None and target == approved_destination(source, event)
        decisions.append((source, event, target if allowed else None))
        return target if allowed else None

    if stage in TIMEOUT_STEPS:
        source = TIMEOUT_STEPS[stage]
        voice = stage in VOICE_TIMEOUT_PREVIEWS
        state = new_conversation(language, source)
        if source == "check_in" and language == "en":
            set_prompt(state, "web_check_in", reply=default_chat_text(language, "web_check_in"))
        continue_in_chat = voice and voice_timeout_mode(configurations[source]) == CONTINUE_IN_CHAT
        if continue_in_chat:
            preview = _render_result(language, channel, configurations, state, decisions)
            preview["voice"] = []
            preview["voice_fallback"] = ""
        else:
            result = advance(state, "", None, voice=voice, timeout=True, resolve_transition=resolve)
            preview = _render_result(language, channel, configurations, result, decisions)
        return {"timeout": True, "continue_in_chat": continue_in_chat, **preview}

    if stage in RECEIPT_METHODS:
        method = RECEIPT_METHODS[stage]
        status, expired, label = RECEIPT_OUTCOMES[receipt_outcome]
        state = start_receipt(new_conversation(language, "check_in"), "DEMO-REFERENCE", method, wait=True)
        deadline = state["delivery_deadline"]
        now = deadline if expired else deadline - 1
        result = apply_receipt_outcome(state, status, now=now, resolve_transition=resolve)
        return {
            "input_stage": DELIVERY_PENDING, "receipt_scenario": label, "fictional_reference": state["reference"],
            **_render_result(language, channel, configurations, result, decisions),
        }

    configuration = configurations[stage]
    parser_stage = "check_in" if stage == "welcome" else stage
    parser_configuration = configurations[parser_stage]
    switch_to_english = parser_stage == "check_in" and language == "pl" and selects_english(reply)
    contact_entry = stage in CONTACT_STAGES
    needs_delivery = stage == "delivery" or contact_entry or fictional_contact is not None
    needs_room_offer = stage in {"room", "confirm"} or needs_delivery
    state = new_conversation(language, "guests" if needs_room_offer else stage)
    state["stage"] = "guests" if needs_room_offer else stage
    check_in = check_out = None
    if stage not in ("welcome", "check_in"):
        check_in = timezone.localdate() + timedelta(days=1)
        state["check_in"] = check_in.isoformat()
    if stage == "guests" or needs_room_offer:
        check_out = check_in + timedelta(days=2)
        state["check_out"] = check_out.isoformat()
    if needs_room_offer:
        # Prepare a two-guest example independently of the edge for the turn being simulated.
        state = advance(state, "", 2, voice=final_voice_timeout or channel == "voice", room_search=_fictional_room_search)
        if stage != "room":
            state = advance(state, "1", None)
        if needs_delivery:
            state = advance(state, "tak" if language == "pl" else "yes", None)
        if contact_entry:
            state = advance(state, "email" if stage == "email" else "sms", None)
        elif fictional_contact:
            method, candidate = fictional_contact
            state = advance(advance(state, method, None), candidate, None)
    if final_voice_timeout:
        # Later listening windows end locally, without submitting a booking turn.
        preview = _render_result(language, channel, configurations, state, decisions)
        preview.update(
            timeout=True, voice_stopped=True, voice_silence_seconds=silence_window_seconds(configuration),
            context_arrival=check_in.isoformat() if check_in else None,
            context_departure=check_out.isoformat() if check_out else None,
            voice=[], voice_fallback="",
        )
        return preview
    parsed = None if switch_to_english else parse_stay_input(parser_stage, reply, language, check_in=check_in)
    input_parsed = stage == "room" or contact_entry
    if input_parsed:
        parsed = extracted_booking_value(state, reply, parsed)
    model_outcome = None
    if fictional_model_value:
        if switch_to_english:
            model_outcome = "Skipped: the guest selected English."
        elif parsed is not None:
            model_outcome = "Skipped: the built-in parser found a value."
        elif not allows_model_fallback(parser_configuration):
            model_outcome = "Skipped: this step uses the built-in parser only."
        else:
            parsed = fictional_model_value
            model_outcome = "Used as the model fallback value."
    correction = None
    corrections_allowed = allows_parser_corrections(parser_configuration)
    if (
        fictional_correction and corrections_allowed and not switch_to_english
        and (stage != "welcome" or parsed is not None)
    ):
        correction = apply_reviewed_correction(
            {**state, "stage": parser_stage}, reply, parsed, fictional_correction, source=channel,
            room_codes=FICTIONAL_ROOM_CODES,
        )
    if correction is not None:
        parsed = correction
    require_range = parsed is not None and parser_configuration.get(ARRIVAL_INPUT_FIELD) == REQUIRE_ARRIVAL_RANGE
    retry_limit = retry_limit_value(configuration)
    if prior_retries:
        state[RETRY_COUNT] = prior_retries
    result = advance_counted_turn(
        state, reply, parsed, resolve, lambda: retry_limit,
        voice=channel == "voice", input_parsed=input_parsed,
        require_arrival_range=require_range, room_search=_fictional_room_search,
        booking_writer=_fictional_booking,
    )
    arrival = departure = None
    if isinstance(parsed, StayDates):
        arrival, departure = parsed.check_in, parsed.check_out
    elif type(parsed) is date:
        if parser_stage == "check_in":
            arrival = parsed
        else:
            departure = parsed
    return {
        "arrival": arrival.isoformat() if arrival else None,
        "departure": departure.isoformat() if departure else None,
        "context_arrival": check_in.isoformat() if check_in else None,
        "context_departure": check_out.isoformat() if check_out else None,
        "input_stage": stage,
        "fictional_correction": fictional_correction,
        "model_outcome": model_outcome,
        "correction_applied": correction is not None,
        "corrections_allowed": corrections_allowed,
        "retry_limit": retry_limit,
        "prior_retries": prior_retries or 0,
        "resulting_retry_count": result.get(RETRY_COUNT, 0),
        "retry_handover": result.get(RETRY_HANDOVER, False),
        "room_options": [offer["room"] for offer in state.get("choices", [])],
        "quoted_room": state.get("room"),
        "selected_room": result.get("room") if stage == "room" else None,
        "quote_decision": next(
            (event for _, event, target in decisions if target and event in {"approve_quote", "decline_quote"}), None,
        ),
        "delivery_choice": {"email": "Email", "phone": "SMS"}.get(result["stage"]) if stage == "delivery" else None,
        "contact_candidate": result.get("contact_candidate") if contact_entry or any(
            event == "replace_contact" and target for _, event, target in decisions
        ) else None,
        "reviewed_contact": state.get("contact_candidate") if fictional_contact else None,
        "fictional_reference": result.get("reference"),
        "guests": parsed if type(parsed) is int else None,
        **_render_result(language, channel, configurations, result, decisions),
    }


def _render_result(language, channel, configurations, result, decisions):
    if result["language"] != language:
        # English arrival after a Polish language choice uses the built-in prompt.
        language = result["language"]
        configurations = {}
    bindings = browser_prompt_bindings(result, channel)
    overrides = browser_prompt_overrides(language, channel, bindings, (
        (key, configurations[key]) for key in bindings if key in configurations
    ))
    voice = []
    for part in voice_segments(result, overrides=overrides):
        voice.extend(split_voice_parts([part]) if len(part["text"]) > MAX_TEXT_CHARACTERS else [part])
    if not voice and result["stage"] == "welcome":
        voice = [
            {"language": part_language, "text": text, "seconds": 0, "listen": False}
            for part_language, text in WELCOME_PARTS
        ]
    return {
        "decisions": decisions,
        "stage": result["stage"],
        "language": language,
        "chat": render_chat(result, overrides=overrides),
        "voice": voice,
        "voice_fallback": result["reply"],
    }
