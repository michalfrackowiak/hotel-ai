from django.urls import path

from . import views


app_name = "bookings"
urlpatterns = [
    path("availability/", views.availability, name="availability"),
    path("bookings/", views.create_booking, name="create"),
]
