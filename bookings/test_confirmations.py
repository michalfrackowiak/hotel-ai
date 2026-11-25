from datetime import timedelta
from decimal import Decimal
from io import StringIO
from smtplib import SMTPServerDisconnected
from unittest.mock import patch
from uuid import uuid4

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings
from django.utils import timezone
from twilio.base.exceptions import TwilioRestException

from reception.receipt import DELIVERY_PENDING, apply_receipt_outcome, start_receipt

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
        with self.assertLogs("bookings.confirmation_outcomes", level="INFO") as outcomes:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                call_command("send_booking_confirmations", stdout=StringIO())
                call_command("send_booking_confirmations", stdout=StringIO())
                self.assertEqual(outcomes.records, [])
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(
            [record.getMessage() for record in outcomes.records],
            ["booking_confirmation_submission method=sms language=pl outcome=accepted"],
        )
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
        with self.assertLogs("bookings.confirmation_outcomes", level="INFO") as outcomes:
            with self.captureOnCommitCallbacks(execute=True), self.assertRaises(CommandError):
                call_command("send_booking_confirmations", stdout=StringIO())
        self.assertEqual(
            [record.getMessage() for record in outcomes.records],
            ["booking_confirmation_submission method=email language=pl outcome=uncertain"],
        )
        booking.confirmation.refresh_from_db()
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.UNCERTAIN)
        self.assertIsNone(booking.confirmation.accepted_at)
        self.assertEqual(dispatch_confirmations(["email"]), (0, 0))
        send.assert_called_once()

    @patch("bookings.confirmations.send_mail", return_value=0)
    def test_worker_logs_failed_submission_only_after_commit(self, send):
        booking = confirm_booking(**{
            **self.request, "delivery_method": "email", "phone": "", "email": "guest@example.test",
        })[0]
        with self.assertNoLogs("bookings.confirmation_outcomes", level="INFO"):
            with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
                self.assertEqual(dispatch_confirmations(["email"]), (0, 1))
                transaction.set_rollback(True)
        booking.confirmation.refresh_from_db()
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.PENDING)

        with self.assertLogs("bookings.confirmation_outcomes", level="INFO") as outcomes:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(dispatch_confirmations(["email"]), (0, 1))
        self.assertEqual(
            [record.getMessage() for record in outcomes.records],
            ["booking_confirmation_submission method=email language=pl outcome=failed"],
        )
        booking.confirmation.refresh_from_db()
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.FAILED)

    @override_settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=True)
    @patch("bookings.confirmations.TwilioClient")
    def test_exact_twilio_rejection_allows_one_delayed_retry(self, twilio):
        confirmation = confirm_booking(**self.request)[0].confirmation
        send = twilio.return_value.messages.create
        rejected = TwilioRestException(429, "/Messages.json", code=20429)
        send.side_effect = [rejected, rejected]
        started = timezone.now()
        with patch("bookings.confirmations.timezone.now", return_value=started):
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 1))
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.RETRY_DUE)
        self.assertEqual(confirmation.attempt_count, 1)
        self.assertEqual(confirmation.retry_after, started + timedelta(seconds=60))
        self.assertEqual(confirmation.retry_until, started + timedelta(minutes=15))
        with patch("bookings.confirmations.timezone.now", return_value=confirmation.retry_after - timedelta(microseconds=1)):
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        with patch("bookings.confirmations.timezone.now", return_value=confirmation.retry_after):
            with self.settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=False):
                self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 1))
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.FAILED)
        self.assertEqual(confirmation.attempt_count, 2)
        self.assertEqual(send.call_count, 2)

    @override_settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=True)
    @patch("bookings.confirmations._submit")
    def test_retry_expiry_and_active_retry_claim_exclude_duplicate_sends(self, submit):
        confirmation = confirm_booking(**self.request)[0].confirmation
        expired = timezone.now()
        schedule = {
            "status": BookingConfirmation.Status.RETRY_DUE, "attempt_count": 1,
            "retry_after": expired - timedelta(minutes=14), "retry_until": expired,
        }
        BookingConfirmation.objects.filter(pk=confirmation.pk).update(**schedule)
        with patch("bookings.confirmations.timezone.now", return_value=expired):
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        submit.assert_not_called()
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.FAILED)

        BookingConfirmation.objects.filter(pk=confirmation.pk).update(
            **{**schedule, "retry_until": expired + timedelta(minutes=1)},
        )

        def accepted(*args):
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
            return "SM00000000000000000000000000000000", True

        submit.side_effect = accepted
        self.assertEqual(dispatch_confirmations(["sms"]), (1, 0))
        submit.assert_called_once()
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.ACCEPTED)
        self.assertEqual(confirmation.attempt_count, 2)

    @override_settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=True)
    @patch("bookings.confirmations._submit")
    def test_ambiguous_or_unmatched_failures_do_not_schedule(self, submit):
        confirmation = confirm_booking(**self.request)[0].confirmation
        for error, expected in (
            (TwilioRestException(429, "/Messages.json", code=12345), BookingConfirmation.Status.FAILED),
            (TwilioRestException(500, "/Messages.json", code=20429), BookingConfirmation.Status.UNCERTAIN),
            (OSError("disconnected"), BookingConfirmation.Status.UNCERTAIN),
        ):
            with self.subTest(error=type(error)):
                BookingConfirmation.objects.filter(pk=confirmation.pk).update(
                    status=BookingConfirmation.Status.PENDING, attempt_count=0,
                )
                submit.side_effect = error
                self.assertEqual(dispatch_confirmations(["sms"]), (0, 1))
                self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
                confirmation.refresh_from_db()
                self.assertEqual(confirmation.status, expected)
                self.assertIsNone(confirmation.retry_after)

    @override_settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=False)
    @patch("bookings.confirmations._submit", side_effect=TwilioRestException(429, "/Messages.json", code=20429))
    def test_disabled_retry_and_legacy_insert_remain_compatible(self, submit):
        booking = confirm_booking(**self.request)[0]
        booking.confirmation.delete()
        legacy = MigrationExecutor(connection).loader.project_state([
            ("bookings", "0004_room_reservation_availability"),
        ]).apps.get_model("bookings", "BookingConfirmation")
        legacy_row = legacy.objects.create(
            booking_id=booking.pk, method="sms", destination="+12025550100", language="en",
        )
        confirmation = BookingConfirmation.objects.get(pk=legacy_row.pk)
        self.assertEqual(confirmation.attempt_count, 0)
        self.assertEqual(dispatch_confirmations(["sms"]), (0, 1))
        with self.settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=True):
            self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        submit.assert_called_once()
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.FAILED)
        self.assertIsNone(confirmation.retry_after)

    @override_settings(HOTEL_SMS_CONFIRMATION_RETRY_ENABLED=True)
    @patch("bookings.confirmations._submit")
    def test_result_write_failure_leaves_claim_unavailable(self, submit):
        confirmation = confirm_booking(**self.request)[0].confirmation
        query_class = type(BookingConfirmation.objects.all())
        actual_update = query_class.update

        def write_outcome(query, **values):
            if values.get("status") == BookingConfirmation.Status.ACCEPTED:
                raise DatabaseError("simulated write failure")
            return actual_update(query, **values)

        submit.return_value = "SM00000000000000000000000000000000", True
        with patch.object(query_class, "update", write_outcome), self.assertRaises(DatabaseError):
            dispatch_confirmations(["sms"])
        self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        submit.assert_called_once()
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, BookingConfirmation.Status.SENDING)

    def test_retry_waiting_never_uses_sent_copy(self):
        receipt = start_receipt({"language": "en", "revision": "test", "flow": "web"}, "DEMO", "sms", wait=True)
        deadline = receipt["delivery_deadline"]
        waiting = apply_receipt_outcome(receipt, BookingConfirmation.Status.RETRY_DUE, now=deadline - 1)
        self.assertEqual(waiting["stage"], DELIVERY_PENDING)
        completed = apply_receipt_outcome(receipt, BookingConfirmation.Status.RETRY_DUE, now=deadline)
        self.assertEqual(completed["stage"], "completed")
        self.assertEqual(completed["reference"], "DEMO")
        self.assertEqual(completed["prompt_key"], "receipt_unconfirmed")
