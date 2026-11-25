from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, BookingConfirmation, Client, Price, Room
from .services import BookingError, available_rooms, confirm_booking


class OperationsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username="reception-fixture", is_staff=True)
        cls.room = Room.objects.create(code="QA-TWIN", capacity=2)
        cls.guest = Client.objects.create(name="Test Guest", email="guest@example.test")
        Price.objects.create(room=cls.room, nightly_rate=Decimal("120.00"))

    def grant(self, *codenames):
        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="bookings", codename__in=codenames))
        self.client.force_login(self.staff)

    def test_index_shortcuts_follow_model_permissions(self):
        self.grant("view_booking")
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Common workflows")
        self.assertContains(response, reverse("admin:bookings_booking_changelist"))
        self.assertContains(response, "Find confirmed reservations")
        self.assertNotContains(response, "Room inventory")
        self.assertNotContains(response, "Date parser settings")
        self.assertNotContains(response, "Speech assignments")
        self.assertNotContains(response, self.guest.email)
        self.assertEqual(self.client.get(reverse("admin:bookings_room_changelist")).status_code, 403)

    def test_no_model_permissions_means_no_workflow_shortcuts(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, "Common workflows")

    def test_booking_filters_use_hotel_local_dates_and_keep_list_read_only(self):
        self.grant("view_booking")
        today = timezone.localdate()
        bookings = {
            name: Booking.objects.create(
                room=self.room, client=self.guest, guests=2, nightly_rate=Decimal("120.00"),
                idempotency_key=uuid4(),
                check_in=today + timedelta(days=start), check_out=today + timedelta(days=end),
            )
            for name, (start, end) in {
                "arrival": (0, 2), "stay": (-1, 1), "departure": (-2, 0), "upcoming": (2, 3), "past": (-3, -2),
            }.items()
        }
        url = reverse("admin:bookings_booking_changelist")
        for filter_value, expected in (
            ("arriving", {"arrival"}), ("staying", {"arrival", "stay"}),
            ("departing", {"departure"}), ("upcoming", {"upcoming"}),
        ):
            with self.subTest(filter_value=filter_value):
                response = self.client.get(url, {"stay_timing": filter_value})
                self.assertEqual(
                    {item.pk for item in response.context["cl"].result_list},
                    {bookings[name].pk for name in expected},
                )
        self.assertEqual(self.client.get(reverse("admin:bookings_booking_add")).status_code, 403)

    def test_legacy_room_insert_uses_database_default(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO bookings_room (code, capacity) VALUES (%s, %s)",
                ["QA-LEGACY", 2],
            )
        self.assertTrue(Room.objects.get(code="QA-LEGACY").accepts_reservations)

    def test_confirmation_submission_review_filters_read_only_changelist(self):
        self.grant("view_bookingconfirmation")
        status = BookingConfirmation.Status
        today = timezone.localdate()
        confirmations = {}
        for day, (outcome, method, language) in enumerate((
            (status.FAILED, "email", "en"),
            (status.UNCERTAIN, "sms", "pl"),
            (status.PENDING, "email", "en"),
            (status.SENDING, "email", "en"),
            (status.ACCEPTED, "email", "en"),
        )):
            booking = Booking.objects.create(
                room=self.room, client=self.guest, guests=2, nightly_rate=Decimal("120.00"),
                idempotency_key=uuid4(), check_in=today + timedelta(days=day),
                check_out=today + timedelta(days=day + 1),
            )
            confirmations[outcome] = BookingConfirmation.objects.create(
                booking=booking, method=method, language=language, status=outcome,
                destination="+48500111222" if method == "sms" else self.guest.email,
            )

        url = reverse("admin:bookings_bookingconfirmation_changelist")
        response = self.client.get(url)
        self.assertContains(response, "Confirmation counts for the current filters and search")
        self.assertNotContains(response, self.guest.email)
        self.assertNotContains(response, "+48500111222")
        self.assertEqual(response.context["queue_status_labels"], status.labels)
        self.assertEqual(response.context["queue_summary"], [
            ("Email", [1, 1, 1, 1, 0]), ("SMS", [0, 0, 0, 0, 1]),
        ])
        response = self.client.get(url, {"submission_review": "needed"})
        self.assertContains(response, "Failed or uncertain")
        self.assertContains(response, "Provider acceptance records submission only.")
        self.assertEqual(
            {item.pk for item in response.context["cl"].result_list},
            {confirmations[status.FAILED].pk, confirmations[status.UNCERTAIN].pk},
        )
        response = self.client.get(url, {"submission_review": "needed", "method__exact": "sms", "language__exact": "pl"})
        self.assertEqual(list(response.context["cl"].result_list), [confirmations[status.UNCERTAIN]])
        self.assertEqual(response.context["queue_summary"], [
            ("Email", [0, 0, 0, 0, 0]), ("SMS", [0, 0, 0, 0, 1]),
        ])
        response = self.client.get(url, {"q": "no-match-fixture"})
        self.assertEqual(response.context["queue_summary"], [
            ("Email", [0, 0, 0, 0, 0]), ("SMS", [0, 0, 0, 0, 0]),
        ])
        detail = reverse("admin:bookings_bookingconfirmation_change", args=[confirmations[status.FAILED].pk])
        self.assertEqual(self.client.post(detail, {"status": status.PENDING}).status_code, 403)
        self.assertEqual(BookingConfirmation.objects.get(pk=confirmations[status.FAILED].pk).status, status.FAILED)

    def test_admin_room_pause_blocks_new_offers_and_stale_quotes_preserving_replay(self):
        self.grant("change_room")
        arrival = timezone.localdate() + timedelta(days=2)
        stay = {"check_in": arrival, "check_out": arrival + timedelta(days=2), "guests": 2}
        contact = {"name": self.guest.name, "email": self.guest.email, "nightly_rate": Decimal("120.00")}
        first_stay = {**stay, "check_in": arrival + timedelta(days=4), "check_out": arrival + timedelta(days=6)}
        key = uuid4()
        existing, _ = confirm_booking(room_id=self.room.pk, idempotency_key=key, **first_stay, **contact)
        self.assertEqual([offer.room_id for offer in available_rooms(**stay)], [self.room.pk])

        url = reverse("admin:bookings_room_change", args=[self.room.pk])
        form = {"code": self.room.code, "capacity": self.room.capacity, "google_calendar_id": ""}
        self.assertEqual(self.client.post(url, form).status_code, 302)
        self.room.refresh_from_db()
        self.assertFalse(self.room.accepts_reservations)
        self.assertEqual(available_rooms(**stay), [])
        with self.assertRaises(BookingError) as unavailable:
            confirm_booking(room_id=self.room.pk, idempotency_key=uuid4(), **stay, **contact)
        self.assertEqual(unavailable.exception.code, "room_unavailable")
        replay, created = confirm_booking(room_id=self.room.pk, idempotency_key=key, **first_stay, **contact)
        self.assertEqual(replay.pk, existing.pk)
        self.assertFalse(created)
        self.assertEqual(Booking.objects.count(), 1)

        self.assertEqual(self.client.post(url, {**form, "accepts_reservations": "on"}).status_code, 302)
        self.assertEqual([offer.room_id for offer in available_rooms(**stay)], [self.room.pk])
