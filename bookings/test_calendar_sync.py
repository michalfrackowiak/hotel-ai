from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from google.auth.exceptions import DefaultCredentialsError

from .calendar_sync import CALENDAR_SCOPE, GoogleCalendarClient, due_booking_ids, publish_due_bookings
from .models import Booking, CalendarPublication, Price, Room
from .services import confirm_booking


class RoomCalendarTests(TransactionTestCase):
    def setUp(self):
        self.room = Room.objects.create(code="ROOM-2", capacity=2, google_calendar_id="two@group.calendar.google.com")
        self.other_room = Room.objects.create(code="ROOM-6", capacity=6)
        for room in (self.room, self.other_room):
            Price.objects.create(room=room, nightly_rate=Decimal("120.00"))

    def booking(self, room, offset=1):
        check_in = timezone.localdate() + timedelta(days=offset)
        booking, _ = confirm_booking(
            room_id=room.pk,
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            guests=2,
            name="Test Guest",
            email="guest@example.test",
            nightly_rate=Decimal("120.00"),
            idempotency_key=uuid4(),
        )
        return booking

    def sync(self, session):
        with patch.object(GoogleCalendarClient, "connect", return_value=GoogleCalendarClient(session)):
            call_command("sync_room_calendars", stdout=StringIO())

    def test_manual_sync_only_exports_current_stays_with_a_calendar(self):
        booking = self.booking(self.room)
        past = self.booking(self.room, offset=4)
        today = timezone.localdate()
        Booking.objects.filter(pk=past.pk).update(check_in=today - timedelta(days=2), check_out=today)
        other_booking = self.booking(self.other_room)
        session = Mock()
        session.request.return_value.status_code = 200

        self.sync(session)
        self.sync(session)

        session.request.assert_called_once_with(
            "POST",
            "https://www.googleapis.com/calendar/v3/calendars/two%40group.calendar.google.com/events",
            timeout=(5, 15),
            allow_redirects=False,
            json={
                "id": booking.reference.hex,
                "summary": "Reserved",
                "start": {"date": booking.check_in.isoformat()},
                "end": {"date": booking.check_out.isoformat()},
                "transparency": "opaque",
                "extendedProperties": {"private": {"source": "hotel-ai"}},
            },
        )
        publication = CalendarPublication.objects.get()
        self.assertEqual(publication.booking, booking)
        self.assertIsNotNone(publication.published_at)
        self.assertEqual(publication.attempts, 1)

        Room.objects.filter(pk=self.other_room.pk).update(google_calendar_id="six@group.calendar.google.com")
        self.sync(session)
        self.assertEqual(session.request.call_count, 2)
        self.assertIn("six%40group.calendar.google.com", session.request.call_args.args[1])
        self.assertEqual(session.request.call_args.kwargs["json"]["id"], other_booking.reference.hex)
        self.assertEqual(CalendarPublication.objects.filter(published_at__isnull=False).count(), 2)

    def test_retries_after_a_timeout_verify_the_same_event_on_the_original_calendar(self):
        booking = self.booking(self.room)
        session = Mock()
        session.request.side_effect = requests.Timeout("sensitive transport details")
        with self.assertRaises(CommandError):
            self.sync(session)

        publication = CalendarPublication.objects.get(booking=booking)
        self.assertIsNone(publication.published_at)
        self.assertIn("could not be reached", publication.last_error)
        self.assertNotIn("sensitive", publication.last_error)
        self.assertGreater(publication.next_attempt_at, timezone.now())
        self.assertEqual(due_booking_ids(), [])
        first_request = session.request.call_args
        sent_event = first_request.kwargs["json"]

        Room.objects.filter(pk=self.room.pk).update(google_calendar_id=None)
        CalendarPublication.objects.update(next_attempt_at=timezone.now())
        self.assertEqual(due_booking_ids(), [])
        Room.objects.filter(pk=self.room.pk).update(google_calendar_id="new@group.calendar.google.com")
        session.request.reset_mock()
        session.request.side_effect = [
            Mock(status_code=409),
            Mock(status_code=200, json=Mock(return_value=sent_event)),
        ]
        self.sync(session)

        self.assertEqual(session.request.call_args_list[0], first_request)
        self.assertEqual(session.request.call_args_list[1].args, ("GET", f"{first_request.args[1]}/{booking.reference.hex}"))
        publication.refresh_from_db()
        self.assertIsNotNone(publication.published_at)
        self.assertEqual(publication.attempts, 2)
        self.assertEqual(publication.last_error, "")

    def test_corrected_room_mapping_is_used_after_a_definite_insert_rejection(self):
        booking = self.booking(self.room)
        session = Mock()
        session.request.return_value.status_code = 404
        with self.assertRaises(CommandError):
            self.sync(session)

        publication = CalendarPublication.objects.get(booking=booking)
        self.assertFalse(publication.event_may_exist)
        Room.objects.filter(pk=self.room.pk).update(google_calendar_id="correct@group.calendar.google.com")
        CalendarPublication.objects.update(next_attempt_at=timezone.now())
        session.request.return_value.status_code = 200
        self.sync(session)

        publication.refresh_from_db()
        self.assertEqual(publication.calendar_id, "correct@group.calendar.google.com")
        self.assertIsNotNone(publication.published_at)
        self.assertIn("correct%40group.calendar.google.com", session.request.call_args.args[1])

    def test_a_conflicting_event_is_not_marked_published(self):
        self.booking(self.room)
        session = Mock()
        session.request.side_effect = [Mock(status_code=409), Mock(status_code=200, json=Mock(return_value={}))]
        with self.assertRaises(CommandError):
            self.sync(session)

        publication = CalendarPublication.objects.get()
        self.assertIsNone(publication.published_at)
        self.assertIn("different event", publication.last_error)
        self.assertEqual([entry.args[0] for entry in session.request.call_args_list], ["POST", "GET"])

    def test_a_second_worker_cannot_publish_a_claimed_booking(self):
        booking = self.booking(self.room)
        client = Mock()

        def check_other_worker(*args):
            self.assertFalse(connection.in_atomic_block)
            other_client = Mock()
            self.assertEqual(publish_due_bookings(other_client, [booking.pk]), (0, 0))
            other_client.publish.assert_not_called()

        client.publish.side_effect = check_other_worker
        self.assertEqual(publish_due_bookings(client, [booking.pk]), (1, 0))
        client.publish.assert_called_once()

    def test_credentials_are_optional_until_a_booking_is_due(self):
        with patch("bookings.calendar_sync.google.auth.default", side_effect=DefaultCredentialsError) as credentials:
            call_command("sync_room_calendars", stdout=StringIO())
            credentials.assert_not_called()
            self.booking(self.room)
            with self.assertRaisesMessage(CommandError, "credentials could not be loaded"):
                call_command("sync_room_calendars", stdout=StringIO())
            credentials.assert_called_once_with(scopes=[CALENDAR_SCOPE])

        self.assertFalse(CalendarPublication.objects.exists())
