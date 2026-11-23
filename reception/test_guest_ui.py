import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from guest_accounts.models import GuestAccount
from guest_accounts.views import GUEST_SESSION_KEY


@override_settings(
    HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=True,
    HOTEL_GOOGLE_GUEST_HOST="testserver",
    HOTEL_GOOGLE_CLIENT_ID="fictional-guest-ui.apps.googleusercontent.com",
)
class GuestAccountUITests(TestCase):
    def test_bilingual_controls_and_popup_headers_are_scoped_to_reception(self):
        for route in ("reception", "reception-chat", "reception-voice"):
            for language, label in (("en", "Open Google sign-in"), ("pl", "Otwórz logowanie Google")):
                with self.subTest(route=route, language=language):
                    page = self.client.get(reverse(route), {"lang": language}, secure=True)
                    self.assertContains(page, label)
                    self.assertContains(page, 'id="guest-google-prepare"')
                    self.assertContains(page, "guest-sign-in.js")
                    self.assertNotContains(page, "accounts.google.com/gsi/client")
                    self.assertEqual(page.headers["Cross-Origin-Opener-Policy"], "same-origin-allow-popups")
                    self.assertEqual(page.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        health = self.client.get(reverse("health"), secure=True)
        self.assertEqual(health.headers["Cross-Origin-Opener-Policy"], "same-origin")

    def test_disabled_invalid_and_staff_sessions_omit_guest_ui(self):
        def check_hidden():
            page = self.client.get(reverse("reception-chat"), secure=True)
            self.assertNotContains(page, "guest-sign-in.js")
            self.assertNotContains(page, 'id="guest-account"')
            self.assertEqual(page.headers["Cross-Origin-Opener-Policy"], "same-origin")
            self.assertEqual(page.headers["Referrer-Policy"], "same-origin")

        with self.settings(HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=False):
            check_hidden()
        with self.settings(HOTEL_GOOGLE_CLIENT_ID=""):
            check_hidden()
        with self.settings(HOTEL_GOOGLE_GUEST_HOST="pilot.example.test"):
            check_hidden()
        with self.settings(HOTEL_GOOGLE_GUEST_HOST=""):
            check_hidden()
        staff = get_user_model().objects.create_user(username="fictional-staff", is_staff=True)
        self.client.force_login(staff)
        check_hidden()

    def test_signed_in_account_has_sign_out_and_omits_provider_controls(self):
        session = self.client.session
        session[GUEST_SESSION_KEY] = GuestAccount.objects.create(google_subject="fictional-guest-ui").pk
        session.save()
        page = self.client.get(reverse("reception-voice"), {"lang": "pl"})
        self.assertContains(page, "Wyloguj się")
        self.assertNotContains(page, 'id="guest-google-prepare"')

    def test_combined_view_resumes_current_draft_language_when_requested(self):
        self.client.post(
            reverse("reception-message"),
            json.dumps({"language": "pl", "start": True}),
            content_type="application/json",
        )
        url = reverse("reception")
        page = self.client.get(url, {"lang": "en", "continue": "1"})
        self.assertContains(page, '<html lang="pl">')
        self.assertContains(page, "data-resume")
        with self.settings(HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=False):
            page = self.client.get(url, {"lang": "en", "continue": "1"})
        self.assertContains(page, '<html lang="en">')
        self.assertNotContains(page, "data-resume")
