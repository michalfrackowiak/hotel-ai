import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Booking, Client, Price, Room


@override_settings(BOOKING_API_KEY="test-booking-key")
class BookingAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with patch.dict("os.environ", HOTEL_DEMO_ADMIN_USERNAME="", HOTEL_DEMO_ADMIN_PASSWORD=""):
            call_command("seed_demo", stdout=StringIO())
        cls.small_room = Room.objects.get(code="ROOM-2")
        cls.large_room = Room.objects.get(code="ROOM-6")

    def setUp(self):
        self.check_in = timezone.localdate() + timedelta(days=1)
        self.check_out = self.check_in + timedelta(days=2)
        self.stay = {"check_in": self.check_in.isoformat(), "check_out": self.check_out.isoformat(), "guests": 2}
        self.payload = {
            **self.stay,
            "room_id": self.small_room.pk,
            "name": "Alicja Żółć",
            "email": "alicja@example.com",
            "nightly_rate": "120.00",
            "idempotency_key": str(uuid4()),
        }
        self.auth = {"Authorization": "Bearer test-booking-key"}

    def availability(self, **overrides):
        return self.client.get(reverse("bookings:availability"), {**self.stay, **overrides}, headers=self.auth)

    def confirm(self, **overrides):
        return self.client.post(
            reverse("bookings:create"), {**self.payload, **overrides}, content_type="application/json", headers=self.auth
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_availability_quotes_pln_per_room_and_filters_by_capacity(self):
        response = self.availability()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rooms"], [
            {"room_id": self.small_room.pk, "room_code": "ROOM-2", "capacity": 2,
             "nightly_rate": "120.00", "total_price": "240.00", "currency": "PLN"},
            {"room_id": self.large_room.pk, "room_code": "ROOM-6", "capacity": 6,
             "nightly_rate": "200.00", "total_price": "400.00", "currency": "PLN"},
        ])
        self.assertEqual(self.availability(guests=6).json()["rooms"][0]["room_id"], self.large_room.pk)
        self.assertEqual(self.availability(guests=7).json()["rooms"], [])

    def test_confirmation_replays_the_saved_quote_without_creating_duplicates(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get()
        self.assertEqual(booking.client.name, self.payload["name"])
        self.assertEqual(response.json(), {"booking": {
            **self.stay,
            "reference": str(booking.reference),
            "room_id": self.small_room.pk,
            "room_code": "ROOM-2",
            "nightly_rate": "120.00",
            "total_price": "240.00",
            "currency": "PLN",
        }})
        Price.objects.filter(room=self.small_room).update(nightly_rate=Decimal("125.00"))
        with patch("bookings.services.timezone.localdate", return_value=self.check_out):
            replay = self.confirm()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), response.json())
        self.assert_error(self.confirm(email="different@example.com"), 409, "idempotency_conflict")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)

    def test_checkout_is_exclusive_and_overlapping_stay_cannot_book_same_room(self):
        self.assertEqual(self.confirm().status_code, 201)
        self.assertEqual([room["room_code"] for room in self.availability().json()["rooms"]], ["ROOM-6"])
        self.assert_error(self.confirm(idempotency_key=str(uuid4())), 409, "room_unavailable")
        self.assertEqual(self.confirm(
            idempotency_key=str(uuid4()),
            check_in=self.check_out.isoformat(),
            check_out=(self.check_out + timedelta(days=1)).isoformat(),
        ).status_code, 201)
        self.assertEqual(Booking.objects.count(), 2)

    def test_invalid_stays_and_stale_rates_do_not_write_records(self):
        self.assert_error(self.availability(check_in="2026-99-99"), 400, "invalid_dates")
        self.assert_error(self.availability(check_out=self.stay["check_in"]), 400, "invalid_dates")
        self.assert_error(self.availability(check_in=(timezone.localdate() - timedelta(days=1)).isoformat()),
                          400, "invalid_dates")
        self.assert_error(self.confirm(guests=True), 400, "invalid_guests")
        self.assert_error(self.confirm(room_id=2**63), 400, "invalid_room_id")
        self.assert_error(self.confirm(guests=3), 409, "room_unavailable")
        self.assert_error(self.confirm(name=" "), 400, "invalid_name")
        self.assert_error(self.confirm(email="invalid"), 400, "invalid_email")
        self.assert_error(self.confirm(idempotency_key="invalid"), 400, "invalid_idempotency_key")
        self.assert_error(self.confirm(nightly_rate="120_"), 400, "invalid_nightly_rate")
        self.assert_error(self.confirm(nightly_rate="119.00"), 409, "stale_price")
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(Client.objects.exists())

    def test_bearer_authentication_is_required_and_fails_closed_when_unconfigured(self):
        availability_url = reverse("bookings:availability")
        booking_url = reverse("bookings:create")
        response = self.client.get(availability_url, self.stay)
        self.assert_error(response, 401, "unauthorized")
        self.assertEqual(response["WWW-Authenticate"], "Bearer")
        self.assert_error(self.client.post(booking_url, self.payload, content_type="application/json"), 401, "unauthorized")
        with self.settings(BOOKING_API_KEY=""):
            self.assert_error(self.availability(), 401, "unauthorized")
        self.assert_error(self.client.get(booking_url, headers=self.auth), 405, "method_not_allowed")

    def test_invalid_and_oversized_request_bodies_get_json_errors(self):
        booking_url = reverse("bookings:create")
        for body, status, code in (
            ("{" , 400, "invalid_json"),
            ("[]", 400, "invalid_payload"),
            (json.dumps({**self.payload, "guests": float("inf")}), 400, "invalid_json"),
            (" " * 8193, 413, "payload_too_large"),
        ):
            with self.subTest(code=code):
                self.assert_error(
                    self.client.post(booking_url, body, content_type="application/json", headers=self.auth), status, code
                )
        self.assert_error(self.client.post(booking_url, self.payload, headers=self.auth), 415, "unsupported_media_type")
        self.assert_error(self.client.post(
            booking_url, self.payload, content_type="application/json", headers=self.auth, CONTENT_LENGTH="invalid"
        ), 400, "invalid_json")

    def test_admin_can_view_but_cannot_modify_bookings_or_booking_contacts(self):
        self.assertEqual(self.confirm().status_code, 201)
        booking = Booking.objects.get()
        user = get_user_model().objects.create_superuser(username="test-admin")
        self.client.force_login(user)
        for obj, prefix in ((booking, "booking"), (booking.client, "client")):
            with self.subTest(model=prefix):
                detail = reverse(f"admin:bookings_{prefix}_change", args=[obj.pk])
                self.assertEqual(self.client.get(detail).status_code, 200)
                self.assertEqual(self.client.post(detail).status_code, 403)
                self.assertEqual(self.client.get(reverse(f"admin:bookings_{prefix}_add")).status_code, 403)
                self.assertEqual(self.client.get(reverse(f"admin:bookings_{prefix}_delete", args=[obj.pk])).status_code, 403)


class SeedDemoTests(TestCase):
    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_seed_is_idempotent_and_keeps_an_existing_admin_password_and_rate(self):
        with patch.dict("os.environ", HOTEL_DEMO_ADMIN_USERNAME="demo-test", HOTEL_DEMO_ADMIN_PASSWORD="fixture-pass"):
            call_command("seed_demo", stdout=StringIO())
        price = Price.objects.get(room__code="ROOM-2")
        self.assertEqual(price.nightly_rate, Decimal("120.00"))
        price.nightly_rate = Decimal("125.00")
        price.save()
        with patch.dict("os.environ", HOTEL_DEMO_ADMIN_USERNAME="demo-test", HOTEL_DEMO_ADMIN_PASSWORD="different-fixture-pass"):
            call_command("seed_demo", stdout=StringIO())
        user = get_user_model().objects.get(username="demo-test")
        self.assertTrue(user.is_superuser and user.is_staff and user.check_password("fixture-pass"))
        self.assertEqual(Room.objects.count(), 2)
        self.assertEqual(Price.objects.count(), 2)
        price.refresh_from_db()
        self.assertEqual(price.nightly_rate, Decimal("125.00"))

    def test_seed_refuses_to_take_over_existing_account(self):
        user = get_user_model().objects.create_user(username="existing")
        with patch.dict("os.environ", HOTEL_DEMO_ADMIN_USERNAME="existing", HOTEL_DEMO_ADMIN_PASSWORD="fixture-pass"):
            with self.assertRaises(CommandError):
                call_command("seed_demo", stdout=StringIO())
        self.assertFalse(Room.objects.exists())
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
