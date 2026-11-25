from datetime import timedelta

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils import timezone

from .models import Booking, BookingConfirmation, CalendarPublication, Client, Price, Room


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = "__all__"
        help_texts = {
            "code": "Guests see this code when choosing a room.",
            "capacity": "Maximum number of guests allowed in the room.",
            "accepts_reservations": "Clear this to pause new reservations, including previously quoted offers. Existing reservations stay valid.",
            "google_calendar_id": "Optional Google Calendar ID for publishing future reservations for this room.",
        }


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    form = RoomForm
    list_display = ("code", "capacity", "accepts_reservations", "google_calendar_id")
    list_filter = ("accepts_reservations",)
    search_fields = ("code", "google_calendar_id")


class PriceForm(forms.ModelForm):
    class Meta:
        model = Price
        fields = "__all__"
        help_texts = {"nightly_rate": "Rate per room per night in PLN. Saved reservations retain their original rate."}


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    form = PriceForm
    search_fields = ("room__code",)
    list_display = ("room", "nightly_rate")
    list_select_related = ("room",)


class ViewOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Client)
class ClientAdmin(ViewOnlyAdmin):
    list_display = ("name", "email", "phone")
    search_fields = ("name", "email", "phone")


class StayTimingFilter(admin.SimpleListFilter):
    title = "stay timing"
    parameter_name = "stay_timing"

    def lookups(self, request, model_admin):
        return (
            ("arriving", "Arriving today"),
            ("staying", "Staying tonight"),
            ("departing", "Departing today"),
            ("upcoming", "Arriving after today"),
        )

    def queryset(self, request, queryset):
        today = timezone.localdate()
        conditions = {
            "arriving": {"check_in": today},
            "staying": {"check_in__lte": today, "check_out__gt": today},
            "departing": {"check_out": today},
            "upcoming": {"check_in__gt": today},
        }
        conditions_for_filter = conditions.get(self.value())
        return queryset.filter(**conditions_for_filter) if conditions_for_filter else queryset


@admin.register(Booking)
class BookingAdmin(ViewOnlyAdmin):
    list_filter = (StayTimingFilter,)
    list_display = ("reference", "room", "client", "check_in", "check_out", "guests", "total_price")
    list_select_related = ("room", "client")
    search_fields = ("reference", "room__code", "client__name", "client__email", "client__phone")
    readonly_fields = ("reference", "created_at", "total_price")


@admin.register(CalendarPublication)
class CalendarPublicationAdmin(ViewOnlyAdmin):
    list_display = ("booking", "calendar_id", "published_at", "attempts", "last_error")
    list_select_related = ("booking",)
    search_fields = ("booking__reference", "calendar_id")


class SubmissionReviewFilter(admin.SimpleListFilter):
    title = "submission review"
    parameter_name = "submission_review"

    def lookups(self, request, model_admin):
        return (("needed", "Failed or uncertain"),)

    def queryset(self, request, queryset):
        if self.value() == "needed":
            return queryset.filter(status__in=(BookingConfirmation.Status.FAILED, BookingConfirmation.Status.UNCERTAIN))
        return queryset


class SubmissionClaimAgeFilter(admin.SimpleListFilter):
    title = "submission claim age"
    parameter_name = "claim_age"

    def lookups(self, request, model_admin):
        return (("older", "Claimed at least 5 minutes ago"),)

    def queryset(self, request, queryset):
        if self.value() == "older":
            return queryset.filter(
                status=BookingConfirmation.Status.SENDING,
                claimed_at__lte=timezone.now() - timedelta(minutes=5),
            )
        return queryset


@admin.register(BookingConfirmation)
class BookingConfirmationAdmin(ViewOnlyAdmin):
    change_list_template = "admin/bookings/bookingconfirmation/change_list.html"
    list_display = ("booking", "method", "status", "claimed_at", "retry_after", "accepted_at")
    list_filter = (SubmissionReviewFilter, SubmissionClaimAgeFilter, "status", "method", "language")
    list_select_related = ("booking",)
    search_fields = ("booking__reference", "provider_reference")
    readonly_fields = ("attempt_count",)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        context = getattr(response, "context_data", None)
        if context and (changelist := context.get("cl")):
            totals = {
                (method, status): total
                for method, status, total in changelist.queryset.order_by()
                .values("method", "status").annotate(total=Count("pk"))
                .values_list("method", "status", "total")
            }
            context["queue_status_labels"] = BookingConfirmation.Status.labels
            context["queue_summary"] = [
                (label, [totals.get((method, status), 0) for status in BookingConfirmation.Status.values])
                for method, label in BookingConfirmation.Method.choices
            ]
        return response
