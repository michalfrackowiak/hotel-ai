from django.urls import path

from . import views
from .lookup import booking_lookup
from .reservations import availability, reserve


urlpatterns = [
    path("", views.index, name="reception"),
    path("my-booking/", booking_lookup, name="reception-booking"),
    path("availability/", availability, name="reception-availability"),
    path("reserve/", reserve, name="reception-reserve"),
    path("reception/message/", views.message, name="reception-message"),
    path("reception/audio/", views.audio, name="reception-audio"),
    path("reception/transcribe/", views.transcript, name="reception-transcript"),
]
