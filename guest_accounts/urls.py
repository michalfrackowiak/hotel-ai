from django.urls import path

from . import views


urlpatterns = [
    path("session/", views.session_status, name="guest-session"),
    path("google/nonce/", views.google_nonce, name="guest-google-nonce"),
    path("google/sign-in/", views.google_sign_in, name="guest-google-sign-in"),
    path("sign-out/", views.sign_out, name="guest-sign-out"),
]
