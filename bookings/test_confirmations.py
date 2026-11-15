from datetime import timedelta
from decimal import Decimal
from io import StringIO
from smtplib import SMTPServerDisconnected
from unittest.mock import patch
from uuid import uuid4

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .confirmations import dispatch_confirmations
from .models import Booking, BookingConfirmation, Client, Price, Room
from .services import BookingError, confirm_booking, normalize_phone


class BookingConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.room = Room.objects.create(code="DEMO", capacity=2)
        Price.objects.create(room=cls.room, nightly_rate="120.00")

    def setUp(self):
        check_in = timezone.localdate() + timedelta(days=2)
        self.request = {
            "room_id": self.room.pk,
            "check_in": check_in,
            "check_out": check_in + timedelta(days=3),
            "guests": 2,
            "name": "",
            "email": "",
            "phone": "501 502 503",
            "nightly_rate": Decimal("120"),
            "idempotency_key": uuid4(),
            "delivery_method": "sms",
            "language": "pl",
        }

    def test_confirming_with_a_phone_creates_one_booking_and_one_delivery_record(self):
        booking, created = confirm_booking(**self.request)
        retry, retry_created = confirm_booking(**{**self.request, "phone": "+48501502503"})
        self.assertTrue(created)
        self.assertFalse(retry_created)
        self.assertEqual(retry.reference, booking.reference)
        self.assertEqual(booking.client.phone, "+48501502503")
        self.assertEqual(booking.client.email, "")
        self.assertEqual(booking.confirmation.method, "sms")
        self.assertEqual(booking.confirmation.language, "pl")
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.PENDING)
        self.assertEqual(BookingConfirmation.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)
        with self.assertRaisesMessage(BookingError, "belongs to a different booking"):
            confirm_booking(**{**self.request, "phone": "+48501502504"})
        self.assertEqual(Booking.objects.count(), 1)

    def test_invalid_contact_creates_no_booking_or_delivery(self):
        for invalid in ({"phone": "123456789 ext 4"}, {"email": "extra@example.test"}, {"delivery_method": ""}):
            with self.subTest(invalid=invalid), self.assertRaises(BookingError):
                confirm_booking(**{**self.request, **invalid})
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(BookingConfirmation.objects.exists())

    def test_phone_formats(self):
        for value in ("+48 501 502 503", "0048 501 502 503", "48501502503", "501502503"):
            with self.subTest(value=value):
                self.assertEqual(normalize_phone(value), "+48501502503")
        self.assertEqual(normalize_phone("+1 (202) 555-0100"), "+12025550100")
        for value in ("123456", "+0 501 502 503", "+48501502503 and +48501502504"):
            with self.subTest(value=value), self.assertRaises(BookingError):
                normalize_phone(value)

    @override_settings(
        EMAIL_HOST="smtp.example.test", DEFAULT_FROM_EMAIL="resort@example.test",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        HOTEL_TWILIO_ACCOUNT_SID="", HOTEL_TWILIO_AUTH_TOKEN="", HOTEL_TWILIO_SMS_FROM="",
    )
    def test_worker_delivers_configured_email_only_and_records_acceptance(self):
        request = {
            **self.request, "delivery_method": "email", "email": "guest@example.test", "phone": "", "language": "en",
        }
        email = confirm_booking(**request)[0]
        Client.objects.filter(pk=email.client_id).update(email="other@example.test")
        self.assertEqual(confirm_booking(**request), (email, False))
        sms = confirm_booking(**{
            **self.request, "idempotency_key": uuid4(),
            "check_in": self.request["check_in"] + timedelta(days=3),
            "check_out": self.request["check_out"] + timedelta(days=3),
        })[0]
        for _ in range(2):
            call_command("send_booking_confirmations", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["guest@example.test"])
        self.assertIn(str(email.reference), mail.outbox[0].body)
        self.assertIn("360.00 PLN", mail.outbox[0].body)
        email.confirmation.refresh_from_db()
        self.assertEqual(email.confirmation.status, BookingConfirmation.Status.ACCEPTED)
        self.assertIsNotNone(email.confirmation.accepted_at)
        self.assertEqual(sms.confirmation.status, BookingConfirmation.Status.PENDING)

    @override_settings(
        EMAIL_HOST="smtp.example.test", DEFAULT_FROM_EMAIL="bad@example.test\n",
        HOTEL_TWILIO_ACCOUNT_SID="", HOTEL_TWILIO_AUTH_TOKEN="", HOTEL_TWILIO_SMS_FROM="",
    )
    def test_malformed_sender_configuration_leaves_email_pending(self):
        booking = confirm_booking(**{
            **self.request, "delivery_method": "email", "phone": "", "email": "guest@example.test",
        })[0]
        with self.assertRaisesMessage(CommandError, "Configure an SMTP or Twilio SMS sender"):
            call_command("send_booking_confirmations", stdout=StringIO())
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.PENDING)

    @override_settings(
        HOTEL_TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
        HOTEL_TWILIO_AUTH_TOKEN="test-token", HOTEL_TWILIO_SMS_FROM="Neptun",
        EMAIL_HOST="", DEFAULT_FROM_EMAIL="",
    )
    @patch("bookings.confirmations.TwilioClient")
    def test_worker_submits_localized_sms(self, twilio):
        booking = confirm_booking(**self.request)[0]
        send = twilio.return_value.messages.create
        send.return_value.status = "queued"
        send.return_value.sid = "SM00000000000000000000000000000000"
        call_command("send_booking_confirmations", stdout=StringIO())
        send.assert_called_once()
        params = send.call_args.kwargs
        self.assertEqual(params["to"], "+48501502503")
        self.assertEqual(params["from_"], "Neptun")
        self.assertIn("Potwierdzenie rezerwacji", params["body"])
        self.assertIn(str(booking.reference), params["body"])
        booking.confirmation.refresh_from_db()
        self.assertEqual(booking.confirmation.provider_reference, send.return_value.sid)
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.ACCEPTED)

    @override_settings(EMAIL_HOST="smtp.example.test", DEFAULT_FROM_EMAIL="resort@example.test")
    @patch("bookings.confirmations.send_mail", side_effect=SMTPServerDisconnected)
    def test_ambiguous_send_is_visible_and_repeated_runs_skip_it(self, send):
        booking = confirm_booking(**{
            **self.request, "delivery_method": "email", "phone": "", "email": "guest@example.test",
        })[0]
        with self.assertRaises(CommandError):
            call_command("send_booking_confirmations", stdout=StringIO())
        booking.confirmation.refresh_from_db()
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.UNCERTAIN)
        self.assertIsNone(booking.confirmation.accepted_at)
        self.assertEqual(dispatch_confirmations(["email"]), (0, 0))
        send.assert_called_once()
