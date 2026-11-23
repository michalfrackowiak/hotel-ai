import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from google.auth import crypt, jwt

from reception.models import Conversation, StaffHandover
from reception.session_continuity import CONVERSATION_SESSION_KEY

from .google_identity import (
    GOOGLE_CERTIFICATES_CACHE, GOOGLE_CERTIFICATES_URL, GOOGLE_TRANSPORT_TIMEOUT, InvalidCredential, _certificates,
    verify_google_credential,
)
from .models import GoogleLoginChallenge, GuestAccount
from .views import GUEST_SESSION_AGE, GUEST_SESSION_KEY, MAX_CHALLENGE_ATTEMPTS, VerificationUnavailable


CLIENT_ID = "fixture.apps.googleusercontent.com"
CREDENTIAL = "fixture.token.signature"
SUBJECT = "fictional-google-subject"


@override_settings(HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=True, HOTEL_GOOGLE_CLIENT_ID=CLIENT_ID)
class GoogleGuestSignInTests(TestCase):
    def message(self, client=None, **payload):
        return (client or self.client).post(
            reverse("reception-message"), {"language": "en", **payload}, content_type="application/json",
        )

    def credential(self, client=None, credential=CREDENTIAL):
        return (client or self.client).post(
            reverse("guest-google-sign-in"), {"credential": credential}, content_type="application/json",
        )

    def issue(self, client=None):
        response = (client or self.client).post(reverse("guest-google-nonce"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["client_id"], CLIENT_ID)
        self.assertIn("no-store", response.headers["Cache-Control"])
        return response.json()["nonce"]

    def login_guest(self, subject=SUBJECT, client=None):
        nonce = self.issue(client)
        with patch("guest_accounts.views.verify_google_credential", return_value=(subject, nonce)) as verify:
            result = self.credential(client)
        verify.assert_called_once_with(CREDENTIAL, CLIENT_ID)
        self.assertEqual(result.status_code, 200, result.content)
        return result

    def clone_cookie(self, client=None):
        clone = Client()
        clone.cookies[settings.SESSION_COOKIE_NAME] = (client or self.client).session.session_key
        return clone

    def test_disabled_and_malformed_configuration_leave_anonymous_booking_available(self):
        for enabled, client_id in ((False, CLIENT_ID), (True, ""), (True, "https://not-a-google-client.test")):
            with self.subTest(enabled=enabled, client_id=client_id), override_settings(
                HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=enabled, HOTEL_GOOGLE_CLIENT_ID=client_id,
            ):
                self.assertEqual(self.client.get(reverse("guest-session")).status_code, 404)
                self.assertEqual(self.client.post(reverse("guest-google-nonce")).status_code, 404)
                self.assertEqual(self.credential().status_code, 404)
                self.assertEqual(self.client.post(reverse("guest-sign-out")).status_code, 404)
                self.assertEqual(self.message(start=True).json()["stage"], "check_in")
        self.assertFalse(GoogleLoginChallenge.objects.exists())
        self.assertFalse(GuestAccount.objects.exists())

    def test_staff_session_and_guest_mutations_are_separate(self):
        staff = get_user_model().objects.create_user(username="fixture-staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("guest-session")).status_code, 403)
        self.assertEqual(self.client.post(reverse("guest-google-nonce")).status_code, 403)
        self.assertEqual(self.credential().status_code, 403)
        self.assertEqual(self.client.post(reverse("guest-sign-out")).status_code, 403)
        self.assertEqual(self.client.session["_auth_user_id"], str(staff.pk))
        self.assertFalse(GuestAccount.objects.exists())

    def test_csrf_is_required_and_rotates_after_sign_in(self):
        browser = Client(enforce_csrf_checks=True)
        for route in ("guest-google-nonce", "guest-google-sign-in", "guest-sign-out"):
            self.assertEqual(browser.post(reverse(route)).status_code, 403)
        csrf = browser.get(reverse("reception-chat")).cookies[settings.CSRF_COOKIE_NAME].value
        nonce = browser.post(reverse("guest-google-nonce"), headers={"X-CSRFToken": csrf}).json()["nonce"]
        with patch("guest_accounts.views.verify_google_credential", return_value=(SUBJECT, nonce)):
            response = browser.post(
                reverse("guest-google-sign-in"), {"credential": CREDENTIAL}, content_type="application/json",
                headers={"X-CSRFToken": csrf},
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(browser.cookies[settings.CSRF_COOKIE_NAME].value, csrf)
        self.assertEqual(browser.post(reverse("guest-sign-out"), headers={"X-CSRFToken": csrf}).status_code, 403)
        self.assertEqual(browser.get(reverse("guest-session")).json(), {"signed_in": True})

    def test_login_preserves_existing_staff_handover_and_legacy_expiry(self):
        opening = self.message(start=True, voice=True).json()
        handover = self.message(voice=True, handover=True, revision=opening["revision"]).json()
        thread = StaffHandover.objects.get()
        reply = thread.messages.create(staff=True, text="Fixture staff answer")
        legacy_session = self.client.session
        del legacy_session[CONVERSATION_SESSION_KEY]
        legacy_session.save()
        original_expiry = Session.objects.get(pk=legacy_session.session_key).expire_date
        Conversation.objects.filter(pk=thread.conversation_id).update(expires_at=None)
        old = self.clone_cookie()

        response = self.login_guest()
        self.assertEqual(response.json(), {"signed_in": True, "draft_preserved": True})
        self.assertNotEqual(self.client.session.session_key, legacy_session.session_key)
        self.assertFalse(Session.objects.filter(pk=legacy_session.session_key).exists())
        self.assertEqual(Conversation.objects.get(pk=thread.conversation_id).expires_at, original_expiry)
        poll = {"poll": True, "cursor": 0, "revision": handover["revision"]}
        self.assertEqual(self.message(old, **poll).status_code, 404)
        self.assertEqual(self.message(**poll).json()["handover_messages"][0]["id"], reply.pk)
        self.assertEqual(self.client.get(reverse("guest-session")).json(), {"signed_in": True})
        self.assertIn("no-store", self.client.get(reverse("guest-session")).headers["Cache-Control"])
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_same_subject_keeps_draft_switch_and_logout_clear_association(self):
        first = self.login_guest()
        self.assertFalse(first.json()["draft_preserved"])
        stay = self.message(start=True).json()
        draft = self.message(message="tomorrow", revision=stay["revision"]).json()
        account = self.client.session[GUEST_SESSION_KEY]
        same = self.login_guest()
        self.assertTrue(same.json()["draft_preserved"])
        self.assertEqual(self.client.session[GUEST_SESSION_KEY], account)
        self.assertEqual(self.message(start=True, resume=True).json(), draft)
        self.assertEqual(GuestAccount.objects.count(), 1)

        former = self.clone_cookie()
        switched = self.login_guest(subject="different-fictional-subject")
        self.assertFalse(switched.json()["draft_preserved"])
        self.assertNotEqual(self.client.session[GUEST_SESSION_KEY], account)
        self.assertFalse(former.get(reverse("guest-session")).json()["signed_in"])
        self.assertEqual(self.message(start=True, resume=True).json()["stage"], "check_in")

        signed_in = self.clone_cookie()
        with override_settings(HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED=False):
            signed_out = self.client.post(reverse("guest-sign-out"))
            self.assertEqual(signed_out.json(), {"signed_in": False})
        self.assertFalse(signed_in.get(reverse("guest-session")).json()["signed_in"])
        self.assertEqual(GuestAccount.objects.count(), 2)
        self.assertEqual(self.message(start=True, resume=True).json()["stage"], "check_in")

    def test_expired_guest_session_cannot_read_or_sign_in_with_previous_challenge(self):
        self.login_guest()
        self.issue()
        session_key = self.client.session.session_key
        expires = Session.objects.get(pk=session_key).expire_date
        self.assertLessEqual(expires, timezone.now() + GUEST_SESSION_AGE)
        self.assertGreater(expires, timezone.now() + GUEST_SESSION_AGE - timedelta(seconds=5))
        Session.objects.filter(pk=session_key).update(expire_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(reverse("guest-session")).json(), {"signed_in": False})
        self.assertEqual(self.credential().status_code, 409)

    def test_nonce_reissue_does_not_reset_attempts_and_stale_tokens_cannot_finish(self):
        draft = self.message(start=True).json()
        old_nonce = self.issue()
        with patch("guest_accounts.views.verify_google_credential", return_value=(SUBJECT, "wrong")):
            for _ in range(MAX_CHALLENGE_ATTEMPTS):
                rejected = self.credential()
                self.assertEqual(rejected.json()["error"], "invalid_credential")
        self.assertEqual(self.client.post(reverse("guest-google-nonce")).status_code, 429)
        self.assertEqual(self.credential().status_code, 429)
        GoogleLoginChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.credential().status_code, 409)
        self.assertNotEqual(self.issue(), old_nonce)
        with patch("guest_accounts.views.verify_google_credential", return_value=(SUBJECT, old_nonce)):
            self.assertEqual(self.credential().status_code, 401)
        self.assertEqual(self.message(start=True, resume=True).json(), draft)
        self.assertFalse(GuestAccount.objects.exists())

    def test_missing_body_oversize_and_provider_failure_do_not_create_account(self):
        draft = self.message(start=True).json()
        nonce = self.issue()
        with patch("guest_accounts.views.verify_google_credential", side_effect=VerificationUnavailable) as verify:
            self.assertEqual(self.credential(credential="bad-token").status_code, 400)
            self.assertEqual(self.credential(credential="a" * 5000 + ".b.c").status_code, 400)
            verify.assert_not_called()
            self.assertEqual(self.credential().status_code, 503)
            verify.assert_called_once()
        self.assertEqual(self.issue(), nonce)
        self.assertEqual(self.message(start=True, resume=True).json(), draft)
        self.assertFalse(GuestAccount.objects.exists())

    def test_parallel_verification_can_only_consume_once(self):
        nonce = self.issue()
        stale_cookie = self.clone_cookie()

        def verify(*_):
            with patch("guest_accounts.views.verify_google_credential", return_value=(SUBJECT, nonce)):
                winner = self.credential(stale_cookie)
            self.assertEqual(winner.status_code, 200, winner.content)
            return SUBJECT, nonce

        with patch("guest_accounts.views.verify_google_credential", side_effect=verify):
            self.assertEqual(self.credential().status_code, 409)
        self.assertEqual(GuestAccount.objects.count(), 1)
        self.assertFalse(GoogleLoginChallenge.objects.exists())
        self.assertEqual(stale_cookie.get(reverse("guest-session")).json(), {"signed_in": True})
        self.assertEqual(self.client.get(reverse("guest-session")).json(), {"signed_in": False})

    def test_returns_same_guest_across_browsers_without_using_profile_claims(self):
        first = self.client
        second = Client()
        self.login_guest(client=first)
        self.login_guest(client=second)
        self.assertEqual(first.session[GUEST_SESSION_KEY], second.session[GUEST_SESSION_KEY])
        self.assertEqual(list(GuestAccount.objects.values_list("google_subject", flat=True)), [SUBJECT])


class GoogleVerificationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.signer = crypt.RSASigner(key, "fictional-key")
        cls.certificates = json.dumps({"fictional-key": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()}).encode()

    def test_signed_fictional_tokens_are_checked_for_audience_issuer_expiry_and_claims(self):
        now = int(time.time())
        base = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": SUBJECT, "nonce": "fixture",
                "iat": now - 20, "exp": now + 60}
        reply = SimpleNamespace(status=200, data=self.certificates)
        with patch("guest_accounts.google_identity._certificates", return_value=reply):
            token = jwt.encode(self.signer, base).decode()
            self.assertEqual(verify_google_credential(token, CLIENT_ID), (SUBJECT, "fixture"))
            for invalid in (
                {"aud": "another-client.apps.googleusercontent.com"}, {"iss": "https://invalid.test"},
                {"exp": now - 1}, {"sub": ""}, {"nonce": None},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(InvalidCredential):
                    verify_google_credential(jwt.encode(self.signer, base | invalid).decode(), CLIENT_ID)
            with self.assertRaises(InvalidCredential):
                verify_google_credential("fixture.token.signature", CLIENT_ID)

    def test_google_certificates_use_bounded_transport_and_respect_cache_age(self):
        cache.delete(GOOGLE_CERTIFICATES_CACHE)
        self.addCleanup(cache.delete, GOOGLE_CERTIFICATES_CACHE)
        with patch("guest_accounts.google_identity.Request") as request:
            response = SimpleNamespace(
                status=200, headers={"Cache-Control": "public, max-age=600", "Age": "12"}, data=self.certificates,
            )
            transport = request.return_value.__enter__.return_value
            transport.return_value = response
            with patch.object(cache, "set", wraps=cache.set) as cache_set:
                self.assertIs(_certificates(GOOGLE_CERTIFICATES_URL), response)
                cache_set.assert_called_once_with(GOOGLE_CERTIFICATES_CACHE, self.certificates, timeout=588)
            transport.assert_called_once_with(
                GOOGLE_CERTIFICATES_URL, timeout=GOOGLE_TRANSPORT_TIMEOUT, allow_redirects=False,
            )
            self.assertEqual(_certificates(GOOGLE_CERTIFICATES_URL).data, self.certificates)
            self.assertEqual(transport.call_count, 1)
            cache.delete(GOOGLE_CERTIFICATES_CACHE)
            response.headers = {"Cache-Control": "no-store, max-age=600"}
            _certificates(GOOGLE_CERTIFICATES_URL)
            self.assertIsNone(cache.get(GOOGLE_CERTIFICATES_CACHE))
