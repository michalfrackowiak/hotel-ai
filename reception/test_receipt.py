from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.confirmations import dispatch_confirmations
from bookings.models import Booking, BookingConfirmation

from .receipt import DELIVERY_WAIT_SECONDS


@override_settings(HOTEL_CONFIRMATION_WAIT_METHODS=("email", "sms"))
class DeliveryReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def post(self, payload, client=None):
        return (client or self.client).post(reverse("reception-message"), payload, content_type="application/json")

    def collect_contact(self, language, turns, *, voice=False):
        state = self.post({"language": language, "start": True}).json()
        if state["stage"] == "welcome":
            state = self.post({"language": language, "timeout": True, "revision": state["revision"]}).json()
        for text in turns:
            payload = {"language": language, "message": text, "revision": state["revision"]}
            if voice:
                payload["voice"] = True
            state = self.post(payload).json()
        self.assertEqual(state["stage"], "delivery_pending")
        return state

    def poll(self, state, *, client=None):
        return self.post({
            "language": state["language"], "poll": True, "revision": state["revision"], "cursor": 0,
        }, client=client)

    @patch("bookings.confirmations._submit", return_value=("", True))
    def test_english_chat_uses_sent_closing_only_after_a_single_submission(self, submit):
        pending = self.collect_contact(
            "en", ("tomorrow", "for 3 nights", "2", "1", "yes", "email", "guest@example.test", "yes"),
        )
        self.assertEqual(self.poll(pending).json(), pending)
        self.assertEqual(self.poll(pending, client=Client()).status_code, 404)
        self.assertNotIn("We have sent", pending["reply"])
        submit.assert_not_called()

        self.assertEqual(dispatch_confirmations(["email"]), (1, 0))
        completed = self.poll(pending).json()
        self.assertEqual(completed["stage"], "completed")
        self.assertNotEqual(pending["revision"], completed["revision"])
        self.assertEqual(pending["reference"], completed["reference"])
        self.assertIn("We have sent the details to your email address.", completed["reply"])
        self.assertIn(completed["reference"], completed["reply"])
        self.assertEqual(self.poll(pending).json()["reference"], completed["reference"])
        self.assertEqual(Booking.objects.count(), 1)
        submit.assert_called_once()

    @patch("bookings.confirmations._submit", return_value=("fixture-message", False))
    def test_polish_browser_voice_reports_unconfirmed_sms_on_provider_rejection(self, _submit):
        pending = self.collect_contact(
            "pl", ("jutro", "na trzy noce", "dwie osoby", "1", "potwierdzam", "SMS", "500 000 123", "tak"),
            voice=True,
        )
        self.assertEqual(dispatch_confirmations(["sms"]), (0, 1))
        completed = self.poll(pending).json()
        self.assertEqual(completed["stage"], "completed")
        self.assertIn("Nie udało się potwierdzić wysłania", completed["reply"])
        self.assertNotIn("Wysłaliśmy SMS", completed["reply"])
        self.assertIn(completed["reference"], completed["reply"])
        self.assertEqual(BookingConfirmation.objects.get().status, BookingConfirmation.Status.FAILED)

    def test_timeout_keeps_the_confirmation_in_the_worker_queue(self):
        pending = self.collect_contact(
            "en", ("tomorrow", "for 3 nights", "2", "1", "confirm", "email", "guest@example.test", "yes"),
        )
        later = timezone.now() + timedelta(seconds=DELIVERY_WAIT_SECONDS + 1)
        with patch("reception.receipt.timezone.now", return_value=later):
            completed = self.poll(pending).json()
        self.assertEqual(completed["stage"], "completed")
        self.assertIn("awaiting dispatch", completed["reply"])
        self.assertNotIn("We have sent", completed["reply"])
        self.assertIn(pending["reference"], completed["reply"])
        self.assertEqual(BookingConfirmation.objects.get().status, BookingConfirmation.Status.PENDING)

    @patch("bookings.confirmations._submit", return_value=("fixture-message", True))
    def test_pending_receipt_survives_rotation_and_rejects_old_session(self, submit):
        pending = self.collect_contact(
            "en", ("tomorrow", "for 3 nights", "2", "1", "yes", "email", "guest@example.test", "yes"),
        )
        session = self.client.session
        old_client = Client()
        old_client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        session.cycle_key()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

        self.assertEqual(self.poll(pending, client=old_client).status_code, 404)
        self.assertEqual(self.poll(pending).json(), pending)
        self.assertEqual(dispatch_confirmations(["email"]), (1, 0))
        completed = self.poll(pending).json()
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["reference"], pending["reference"])
        self.assertEqual(Booking.objects.count(), 1)
        submit.assert_called_once()
