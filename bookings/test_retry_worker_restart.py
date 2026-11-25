import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase


WORKER_CASE = """
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys
from unittest.mock import patch
from uuid import uuid4

import django
django.setup()

from django.core.management import call_command
from django.core.management.base import CommandError
from twilio.base.exceptions import TwilioRestException

from bookings.models import BookingConfirmation, Price, Room
from bookings.services import confirm_booking

phase = sys.argv[1]
now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
if phase == 'schedule':
    call_command('migrate', interactive=False, verbosity=0)
    room = Room.objects.create(code='RESTART', capacity=2)
    price = Price.objects.create(room=room, nightly_rate=Decimal('120.00'))
    arrival = datetime.now(timezone.utc).date() + timedelta(days=7)
    confirm_booking(
        room_id=room.pk, check_in=arrival, check_out=arrival + timedelta(days=2),
        guests=2, name='', email='', phone='+48501502503',
        nightly_rate=price.nightly_rate, delivery_method='sms', language='en', idempotency_key=uuid4(),
    )

with patch('bookings.confirmations.TwilioClient') as client:
    send = client.return_value.messages.create
    if phase == 'schedule':
        send.side_effect = TwilioRestException(429, '/Messages.json', code=20429)
        with patch('bookings.confirmations.timezone.now', return_value=now):
            try:
                call_command('send_booking_confirmations')
            except CommandError as error:
                assert 'unsuccessful attempts' in str(error), error
            else:
                raise AssertionError('Expected original submission to be rejected')
        confirmation = BookingConfirmation.objects.get()
        assert confirmation.status == 'retry_due' and confirmation.attempt_count == 1
        assert confirmation.retry_after == now + timedelta(seconds=60)
        send.assert_called_once()
    else:
        confirmation = BookingConfirmation.objects.get()
        send.return_value.sid = 'SMfictional'
        send.return_value.status = 'queued'
        if phase == 'resume':
            assert confirmation.status == 'retry_due' and confirmation.attempt_count == 1
            with patch('bookings.confirmations.timezone.now', return_value=confirmation.retry_after - timedelta(seconds=1)):
                call_command('send_booking_confirmations')
            send.assert_not_called()
            confirmation.refresh_from_db()
            assert confirmation.status == 'retry_due' and confirmation.attempt_count == 1
        with patch('bookings.confirmations.timezone.now', return_value=confirmation.retry_after):
            call_command('send_booking_confirmations')
        confirmation.refresh_from_db()
        assert confirmation.status == 'accepted' and confirmation.attempt_count == 2
        assert confirmation.provider_reference == 'SMfictional'
        if phase == 'resume':
            send.assert_called_once()
        else:
            send.assert_not_called()
"""


class SmsRetryWorkerRestartTests(SimpleTestCase):
    def test_retry_survives_worker_restart_and_is_not_sent_again(self):
        with TemporaryDirectory(prefix="hotel-sms-retry-") as directory:
            env = {
                **os.environ,
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DJANGO_DB_PATH": str(Path(directory) / "booking.sqlite3"),
                "DJANGO_SECRET_KEY": "django-insecure-sms-retry-test-fixture",
                "HOTEL_SMTP_HOST": "",
                "HOTEL_TWILIO_ACCOUNT_SID": "ACfictional",
                "HOTEL_TWILIO_AUTH_TOKEN": "fictional-token",
                "HOTEL_TWILIO_SMS_FROM": "+48501502504",
                "HOTEL_SMS_CONFIRMATION_RETRY_ENABLED": "1",
            }
            for phase in ("schedule", "resume", "finished"):
                result = subprocess.run(
                    [sys.executable, "-c", WORKER_CASE, phase],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, f"{phase}:\n{result.stdout}\n{result.stderr}")
