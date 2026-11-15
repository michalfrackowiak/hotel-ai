from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from django import forms
from django.core import signing
from django.shortcuts import render
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from bookings.services import BookingError, available_rooms, confirm_booking


OFFER_SALT = "hotel-ai.reception.room-offer"
OFFER_MAX_AGE = 30 * 60


class StaySearch(forms.Form):
    check_in = forms.DateField(input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date"}))
    check_out = forms.DateField(input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date"}))
    guests = forms.IntegerField(min_value=1, max_value=32767)

    def clean(self):
        stay = super().clean()
        arrival, departure = stay.get("check_in"), stay.get("check_out")
        if arrival is not None and arrival < timezone.localdate():
            self.add_error("check_in", _("Check-in must be today or later."))
        if arrival is not None and departure is not None and departure <= arrival:
            self.add_error("check_out", _("Check-out must be after check-in."))
        return stay


class GuestContact(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)

    def clean_name(self):
        name = self.cleaned_data["name"]
        if not name.isprintable():
            raise forms.ValidationError(_("Enter a valid guest name."))
        return name


def _offer_token(offer, stay, session):
    return signing.dumps({
        "room_id": offer.room_id,
        "room_code": offer.room_code,
        "check_in": stay["check_in"].isoformat(),
        "check_out": stay["check_out"].isoformat(),
        "guests": stay["guests"],
        "nightly_rate": str(offer.nightly_rate),
        "key": str(uuid4()),
        "session": session,
    }, salt=OFFER_SALT)


@never_cache
@require_safe
def availability(request):
    language = "pl" if request.GET.get("lang") == "pl" else "en"
    submitted = any(field in request.GET for field in StaySearch.base_fields)
    search = StaySearch(request.GET if submitted else None, initial={"guests": 2})
    today = timezone.localdate().isoformat()
    for field in ("check_in", "check_out"):
        search.fields[field].widget.attrs["min"] = today
    choices = []
    error = None
    with translation.override(language):
        searched = search.is_valid()
        if searched:
            stay = search.cleaned_data
            try:
                offers = available_rooms(**stay)
                if offers:
                    session = request.session.get("room_offer_session")
                    if session is None:
                        session = uuid4().hex
                        request.session["room_offer_session"] = session
                    choices = [(offer, _offer_token(offer, stay, session)) for offer in offers]
            except BookingError as problem:
                error = problem.code
        return render(request, "reception/availability.html", {
            "language": language,
            "search": search,
            "searched": searched,
            "choices": choices,
            "error": error,
        })


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def reserve(request):
    submitted = request.method == "POST"
    source = request.POST if submitted else request.GET
    language = "pl" if source.get("lang") == "pl" else "en"
    token = source.get("token", "")
    contact = GuestContact(request.POST if submitted else None)
    stay = None
    booking = None
    error = None
    try:
        if len(token) > 1024:
            raise signing.BadSignature
        offer = signing.loads(token, salt=OFFER_SALT, max_age=OFFER_MAX_AGE)
        if offer["session"] != request.session.get("room_offer_session"):
            raise signing.BadSignature
    except signing.BadSignature:
        error = "offer"
    else:
        arrival = date.fromisoformat(offer["check_in"])
        departure = date.fromisoformat(offer["check_out"])
        rate = Decimal(offer["nightly_rate"])
        stay = {
            "room_code": offer["room_code"], "check_in": offer["check_in"], "check_out": offer["check_out"],
            "guests": offer["guests"], "nightly_rate": rate, "total_price": rate * (departure - arrival).days,
        }
        with translation.override(language):
            if submitted and contact.is_valid():
                try:
                    booking, _ = confirm_booking(
                        room_id=offer["room_id"], check_in=arrival, check_out=departure,
                        guests=offer["guests"], nightly_rate=rate, idempotency_key=UUID(offer["key"]),
                        **contact.cleaned_data,
                    )
                except BookingError as problem:
                    error = problem.code
    with translation.override(language):
        return render(request, "reception/reserve.html", {
            "language": language,
            "stay": stay,
            "token": token if stay is not None else "",
            "contact": contact,
            "booking": booking,
            "error": error,
        })
