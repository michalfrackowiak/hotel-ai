from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Price, Room


class DirectReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.small = Room.objects.create(code="ROOM-2", capacity=2)
        cls.large = Room.objects.create(code="ROOM-6", capacity=6)
        Price.objects.create(room=cls.small, nightly_rate=Decimal("120.00"))
        Price.objects.create(room=cls.large, nightly_rate=Decimal("200.00"))

    def setUp(self):
        self.search_url = reverse("reception-availability")
        self.reserve_url = reverse("reception-reserve")
        arrival = timezone.localdate() + timedelta(days=1)
        self.stay = {"check_in": arrival.isoformat(), "check_out": (arrival + timedelta(days=2)).isoformat(), "guests": 2}
        self.contact = {"name": "Guest Example", "email": "guest@example.test"}

    def test_search_filters_capacity_and_validates_dates(self):
        response = self.client.get(self.search_url, {**self.stay, "guests": 3, "lang": "pl"})
        self.assertEqual(response.context["language"], "pl")
        self.assertEqual([offer.room_id for offer, token in response.context["choices"]], [self.large.pk])
        self.assertContains(response, "400")
        response = self.client.get(self.search_url, {**self.stay, "check_out": self.stay["check_in"]})
        self.assertIn("check_out", response.context["search"].errors)
        self.assertEqual(response.context["choices"], [])

    def test_selects_exact_room_and_replays_confirmation(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(self.search_url, self.stay)
        choices = response.context["choices"]
        self.assertEqual([offer.room_id for offer, token in choices], [self.small.pk, self.large.pk])
        token = choices[1][1]
        form = client.get(self.reserve_url, {"token": token})
        self.assertEqual(form.context["stay"], {
            **self.stay, "room_code": "ROOM-6", "nightly_rate": Decimal("200.00"), "total_price": Decimal("400.00"),
        })
        self.assertFalse(Booking.objects.exists())
        self.assertEqual(client.post(self.reserve_url, {"token": token, **self.contact}).status_code, 403)
        csrf = form.cookies["csrftoken"].value
        for attempt in range(2):
            receipt = client.post(
                self.reserve_url, {"token": token, **self.contact, "room_id": self.small.pk},
                headers={"X-CSRFToken": csrf},
            )
            self.assertEqual(receipt.context["booking"].room_id, self.large.pk)
            self.assertNotContains(receipt, self.contact["email"])
        self.assertEqual(Booking.objects.count(), 1)
        leads = self.client.get(self.search_url, self.stay).context["choices"]
        self.assertEqual([offer.room_id for offer, token in leads], [self.small.pk])

    def test_rejects_foreign_tampered_and_stale_offers(self):
        token = self.client.get(self.search_url, self.stay).context["choices"][0][1]
        for client, candidate in ((Client(), token), (self.client, token + "x")):
            with self.subTest(candidate=candidate):
                response = client.post(self.reserve_url, {"token": candidate, **self.contact})
                self.assertEqual(response.context["error"], "offer")
        Price.objects.filter(room=self.small).update(nightly_rate=Decimal("150.00"))
        response = self.client.post(self.reserve_url, {"token": token, **self.contact})
        self.assertEqual(response.context["error"], "stale_price")
        self.assertFalse(Booking.objects.exists())
