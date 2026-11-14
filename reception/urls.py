from django.urls import path

from . import views


urlpatterns = [
    path("", views.index, name="reception"),
    path("reception/message/", views.message, name="reception-message"),
    path("reception/audio/", views.audio, name="reception-audio"),
]
