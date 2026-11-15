from django.urls import path, re_path

from . import views


app_name = "telephony"
urlpatterns = [
    path("incoming/", views.incoming, name="incoming"),
    re_path(r"^turn/(?P<revision>[0-9a-f]{32})/$", views.turn, name="turn"),
    re_path(r"^handover/(?P<revision>[0-9a-f]{32})/$", views.handover, name="handover"),
]
