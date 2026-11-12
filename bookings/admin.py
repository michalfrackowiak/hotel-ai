from django.contrib import admin

from .models import Booking, Client, Price, Room


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
    list_display = ("name", "email")
    search_fields = ("name", "email")


@admin.register(Booking)
class BookingAdmin(ViewOnlyAdmin):
    list_display = ("reference", "room", "client", "check_in", "check_out", "guests", "total_price")
    list_select_related = ("room", "client")
    search_fields = ("reference", "room__code", "client__name", "client__email")
    readonly_fields = ("reference", "created_at", "total_price")
