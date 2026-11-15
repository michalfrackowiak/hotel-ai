from datetime import date
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from bookings.models import Booking, Client as Guest, Room


class BookingLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.booking = Booking.objects.create(
            room=Room.objects.create(code="ROOM-LOOKUP", capacity=2),
            client=Guest.objects.create(name="Private Example", email="private@example.test"),
            idempotency_key=uuid4(),
            check_in=date(2027, 1, 15),
            check_out=date(2027, 1, 18),
            guests=2,
            nightly_rate="120.00",
        )

    def test_lookup_requires_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse("reception-booking")
        number = str(self.booking.reference)
        response = client.get(url, {"reference": number, "lang": "pl"})
        self.assertNotContains(response, "ROOM-LOOKUP")
        self.assertNotContains(response, number)
        self.assertEqual(client.post(url, {"reference": number}).status_code, 403)
        token = response.cookies["csrftoken"].value
        response = client.post(
            url, {"lang": "pl", "reference": f" {number.upper()} "},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.context["language"], "pl")
        self.assertContains(response, "ROOM-LOOKUP")
        self.assertContains(response, "360")
        self.assertNotContains(response, "Private Example")
        self.assertNotContains(response, "private@example.test")
        self.assertNotContains(response, str(self.booking.idempotency_key))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_invalid_and_unknown_references(self):
        url = reverse("reception-booking")
        for reference, error in (("bad", "invalid"), (str(uuid4()), "missing")):
            with self.subTest(error=error):
                response = self.client.post(url, {"reference": reference})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["error"], error)
                self.assertNotContains(response, "ROOM-LOOKUP")
