from datetime import date, timedelta
from decimal import Decimal

from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Price, Room

from .models import Conversation
from .parsing import parse_stay_input


class StayInputTests(SimpleTestCase):
    def test_localized_month_names_and_ordinals_require_a_year(self):
        self.assertEqual(parse_stay_input("check_in", "15 listopada 2027", "pl"), date(2027, 11, 15))
        self.assertEqual(parse_stay_input("check_out", "November 15th, 2027", "en"), date(2027, 11, 15))
        self.assertIsNone(parse_stay_input("check_in", "31 listopada 2027", "pl"))
        self.assertIsNone(parse_stay_input("check_in", "15 listopada", "pl"))
        self.assertIsNone(parse_stay_input("check_out", "November 15th", "en"))


class ReceptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.double_room = Room.objects.create(code="ROOM-2", capacity=2)
        group_room = Room.objects.create(code="ROOM-6", capacity=6)
        Price.objects.create(room=cls.double_room, nightly_rate=Decimal("120"))
        Price.objects.create(room=group_room, nightly_rate=Decimal("200"))

    def post(self, **payload):
        return self.client.post(reverse("reception-message"), payload, content_type="application/json")

    def turn(self, current, text, expected_stage):
        response = self.post(language=current["language"], revision=current["revision"], message=text)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["stage"], expected_stage)
        return payload

    def quote(self, language="en", *, guests="two guests"):
        today = timezone.localdate()
        check_in = today + timedelta(days=3)
        check_out = check_in + timedelta(days=2)
        fmt = "%d.%m.%Y" if language == "pl" else "%Y-%m-%d"
        current = self.post(language=language, reset=True).json()
        current = self.turn(current, check_in.strftime(fmt), "check_out")
        current = self.turn(current, check_out.strftime(fmt), "guests")
        current = self.turn(current, guests, "name")
        current = self.turn(current, "Ada Example", "email")
        return self.turn(current, "ada@example.com", "confirm")

    def test_english_quote_and_explicit_confirmation(self):
        quote = self.quote()
        self.assertIn("ROOM-2", quote["reply"])
        self.assertIn("120.00 PLN per night", quote["reply"])
        self.assertIn("240.00 PLN", quote["reply"])
        self.assertFalse(Booking.objects.exists())

        quote = self.turn(quote, "Yes?", "confirm")
        self.assertIn("240.00 PLN", quote["reply"])
        self.assertFalse(Booking.objects.exists())

        confirmed = self.turn(quote, "Yes.", "completed")
        self.assertIn(confirmed["reference"], confirmed["reply"])
        self.assertEqual(str(Booking.objects.get().reference), confirmed["reference"])
        self.assertNotIn("email", Conversation.objects.get().state)

        retry = self.post(language="en", revision=quote["revision"], message="Yes.")
        self.assertEqual(retry.status_code, 409)
        self.assertTrue(retry.json()["stale"])
        self.assertEqual(retry.json()["reference"], confirmed["reference"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_polish_quote_and_explicit_confirmation(self):
        quote = self.quote("pl", guests="sześć osób")
        self.assertIn("Pokój ROOM-6", quote["reply"])
        self.assertIn("200,00 PLN za noc", quote["reply"])
        self.assertIn("400,00 PLN", quote["reply"])
        quote = self.turn(quote, "Yes", "confirm")
        self.assertFalse(Booking.objects.exists())
        confirmed = self.turn(quote, "Tak", "completed")
        self.assertIn("Twoja rezerwacja jest potwierdzona", confirmed["reply"])
        self.assertEqual(Booking.objects.get().guests, 6)

    def test_polish_uses_natural_number_words_for_two_guests(self):
        current = self.post(language="pl", start=True).json()
        current = self.turn(current, "dzisiaj", "check_out")
        current = self.turn(current, "jutro", "guests")
        current = self.turn(current, "dwie osoby", "name")
        self.assertIn("120,00 PLN", current["reply"])

    def test_unavailable_stay_recovers_in_both_languages(self):
        for language, check_in, check_out, reply in (
            ("en", "tomorrow", "day after tomorrow", "No room is available"),
            ("pl", "jutro", "pojutrze", "Brak dostępnych pokoi"),
        ):
            with self.subTest(language=language):
                current = self.post(language=language, reset=True).json()
                current = self.turn(current, check_in, "check_out")
                current = self.turn(current, check_out, "guests")
                current = self.turn(current, "7", "check_in")
                self.assertIn(reply, current["reply"])
        self.assertFalse(Booking.objects.exists())

    def test_rejecting_a_quote_clears_the_contact_details(self):
        quote = self.quote()
        current = self.turn(quote, "No", "check_in")
        self.assertIn("cancelled", current["reply"])
        self.assertNotIn("name", Conversation.objects.get().state)
        self.assertFalse(Booking.objects.exists())

    def test_changed_price_requires_fresh_quote(self):
        quote = self.quote()
        Price.objects.filter(room=self.double_room).update(nightly_rate=Decimal("130"))
        current = self.turn(quote, "Yes", "check_in")
        self.assertIn("fresh quote", current["reply"])
        self.assertFalse(Booking.objects.exists())

    def test_start_resumes_and_other_sessions_cannot_confirm(self):
        quote = self.quote()
        self.assertEqual(self.post(language="en", start=True).json(), quote)
        other = Client().post(
            reverse("reception-message"), {"language": "en", "revision": quote["revision"], "message": "Yes"},
            content_type="application/json",
        )
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.json()["stage"], "check_in")
        self.assertNotIn("reference", other.json())
        self.assertFalse(Booking.objects.exists())

    def test_homepage_sets_csrf_cookie_and_endpoint_requires_it(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("reception"), {"lang": "pl"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["language"], "pl")
        csrf = client.cookies["csrftoken"].value
        path = reverse("reception-message")
        payload = {"language": "pl", "start": True}
        self.assertEqual(client.post(path, payload, content_type="application/json").status_code, 403)
        response = client.post(path, payload, content_type="application/json", headers={"x-csrftoken": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dzień dobry", response.json()["reply"])

    def test_rejects_invalid_protocol_data(self):
        self.assertEqual(self.post(language="de", start=True).status_code, 400)
        self.assertEqual(self.post(language="en", message="Yes").status_code, 400)
        self.assertEqual(self.post(language="en", reset=True, start=True).status_code, 400)
