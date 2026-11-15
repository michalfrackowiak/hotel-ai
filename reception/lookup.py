from uuid import UUID

from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from bookings.models import Booking


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def booking_lookup(request):
    source = request.POST if request.method == "POST" else request.GET
    language = "pl" if source.get("lang") == "pl" else "en"
    reference = ""
    booking = None
    error = None
    if request.method == "POST":
        reference = request.POST.get("reference", "").strip()
        try:
            if len(reference) not in (32, 36):
                raise ValueError("Invalid reference length")
            booking_reference = UUID(reference)
        except ValueError:
            error = "invalid"
        else:
            booking = Booking.objects.select_related("room").filter(reference=booking_reference).first()
            if booking is None:
                error = "missing"

    return render(request, "reception/booking.html", {
        "language": language,
        "reference": reference[:36],
        "booking": booking,
        "error": error,
    })
