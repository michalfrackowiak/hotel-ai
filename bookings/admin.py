from django.contrib import admin

from .models import Booking, BookingConfirmation, CalendarPublication, Client, Price, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("code", "capacity", "google_calendar_id")
    search_fields = ("code", "google_calendar_id")


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
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


@admin.register(Booking)
class BookingAdmin(ViewOnlyAdmin):
    list_display = ("reference", "room", "client", "check_in", "check_out", "guests", "total_price")
    list_select_related = ("room", "client")
    search_fields = ("reference", "room__code", "client__name", "client__email", "client__phone")
    readonly_fields = ("reference", "created_at", "total_price")


@admin.register(CalendarPublication)
class CalendarPublicationAdmin(ViewOnlyAdmin):
    list_display = ("booking", "calendar_id", "published_at", "attempts", "last_error")
    list_select_related = ("booking",)
    search_fields = ("booking__reference", "calendar_id")


@admin.register(BookingConfirmation)
class BookingConfirmationAdmin(ViewOnlyAdmin):
    list_display = ("booking", "method", "status", "claimed_at", "accepted_at")
    list_filter = ("status", "method")
    list_select_related = ("booking",)
    search_fields = ("booking__reference", "provider_reference")
