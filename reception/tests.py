from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from bookings.models import Booking, BookingConfirmation, Price, Room

from .models import Conversation, StaffHandover
from .parsing import parse_stay_input


class StayInputTests(SimpleTestCase):
    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 15))
    def test_localized_month_names_roll_to_next_year_if_the_month_passed(self, today):
        self.assertEqual(parse_stay_input("check_in", "15 listopada 2027", "pl"), date(2027, 11, 15))
        self.assertEqual(parse_stay_input("check_out", "November 15th, 2027", "en"), date(2027, 11, 15))
        self.assertIsNone(parse_stay_input("check_in", "31 listopada 2027", "pl"))
        self.assertEqual(parse_stay_input("check_in", "15 listopada", "pl"), date(2027, 11, 15))
        self.assertEqual(parse_stay_input("check_out", "November 15th", "en"), date(2027, 11, 15))


class ReceptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.double_room = Room.objects.create(code="ROOM-2", capacity=2)
        group_room = Room.objects.create(code="ROOM-6", capacity=6)
        Price.objects.create(room=cls.double_room, nightly_rate=Decimal("120"))
        Price.objects.create(room=group_room, nightly_rate=Decimal("200"))

    def post(self, **payload):
        return self.client.post(reverse("reception-message"), payload, content_type="application/json")

    def turn(self, current, text, expected_stage, **flags):
        response = self.post(language=current["language"], revision=current["revision"], message=text, **flags)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["stage"], expected_stage)
        return payload

    def quote(self, language="en", *, guests="two guests", option="1"):
        today = timezone.localdate()
        check_in = today + timedelta(days=3)
        check_out = check_in + timedelta(days=2)
        fmt = "%d.%m.%Y" if language == "pl" else "%Y-%m-%d"
        current = self.post(language=language, reset=True).json()
        current = self.turn(current, check_in.strftime(fmt), "check_out")
        current = self.turn(current, check_out.strftime(fmt), "guests")
        current = self.turn(current, guests, "room")
        return self.turn(current, option, "confirm")

    def test_english_quote_and_explicit_confirmation(self):
        quote = self.quote()
        self.assertIn("ROOM-2", quote["reply"])
        self.assertIn("240.00 PLN", quote["reply"])
        self.assertFalse(Booking.objects.exists())

        quote = self.turn(quote, "Yes, maybe", "confirm")
        self.assertIn("240.00 PLN", quote["reply"])
        self.assertFalse(Booking.objects.exists())

        delivery = self.turn(quote, "Yes.", "delivery")
        email = self.turn(delivery, "Email", "email")
        email = self.turn(email, "invalid@example", "email")
        self.assertFalse(Booking.objects.exists())
        confirmed = self.turn(email, "a d a at example dot com", "completed", voice=True)
        self.assertIn(confirmed["reference"], confirmed["reply"])
        self.assertEqual(str(Booking.objects.get().reference), confirmed["reference"])
        self.assertNotIn("email", Conversation.objects.get().state)
        self.assertEqual(BookingConfirmation.objects.get().method, "email")
        self.assertEqual(Booking.objects.get().client.email, "ada@example.com")

        retry = self.post(language="en", revision=email["revision"], message="ada@example.com")
        self.assertEqual(retry.status_code, 409)
        self.assertTrue(retry.json()["stale"])
        self.assertEqual(retry.json()["reference"], confirmed["reference"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_polish_quote_and_explicit_confirmation(self):
        quote = self.quote("pl", guests="sześć osób")
        self.assertIn("ROOM-6", quote["reply"])
        self.assertIn("400,00 PLN", quote["reply"])
        quote = self.turn(quote, "Yes", "confirm")
        self.assertFalse(Booking.objects.exists())
        delivery = self.turn(quote, "Potwierdzam", "delivery")
        phone = self.turn(delivery, "SMS", "phone")
        phone = self.turn(phone, "123", "phone")
        self.assertFalse(Booking.objects.exists())
        confirmed = self.turn(phone, "500 000 123", "completed")
        self.assertIn("Twoja rezerwacja jest potwierdzona", confirmed["reply"])
        self.assertEqual(Booking.objects.get().guests, 6)
        self.assertEqual(BookingConfirmation.objects.get().method, "sms")
        self.assertEqual(Booking.objects.get().client.phone, "+48500000123")

    def test_polish_uses_natural_number_words_for_two_guests(self):
        current = self.post(language="pl", start=True).json()
        current = self.turn(current, "dzisiaj", "check_out")
        current = self.turn(current, "jutro", "guests")
        current = self.turn(current, "dwie osoby", "room")
        self.assertIn("120,00 PLN", current["reply"])
        self.assertIn("200,00 PLN", current["reply"])
        quote = self.turn(current, "opcja dwa", "confirm")
        self.assertIn("ROOM-6", quote["reply"])

    @patch("django.utils.timezone.localdate", return_value=date(2027, 12, 30))
    def test_checkout_relative_to_today_after_a_separate_arrival(self, today):
        for language, arrival, departure, arrival_text, departure_text, question in (
            (
                "en", "tomorrow", "for two nights", "2027-12-31", "2028-01-02", "number of nights",
            ),
            (
                "pl", "jutro", "na dwie noce", "31.12.2027", "02.01.2028", "na ile nocy",
            ),
        ):
            with self.subTest(language=language):
                current = self.post(language=language, reset=True).json()
                current = self.turn(current, arrival, "check_out")
                self.assertIn(question, current["reply"])
                current = self.turn(current, arrival, "check_out")
                current = self.turn(current, departure, "guests")
                self.assertIn(arrival_text, current["reply"])
                self.assertIn(departure_text, current["reply"])

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
        current = self.turn(quote, "No", "cancelled")
        self.assertIn("Goodbye", current["reply"])
        self.assertNotIn("name", Conversation.objects.get().state)
        self.assertFalse(Booking.objects.exists())

    def test_changed_price_requires_fresh_quote(self):
        quote = self.quote()
        Price.objects.filter(room=self.double_room).update(nightly_rate=Decimal("130"))
        delivery = self.turn(quote, "Confirm", "delivery")
        email = self.turn(delivery, "Email", "email")
        current = self.turn(email, "ada@example.com", "check_in")
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
        response = client.get(reverse("reception"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["language"], "pl")
        english = client.get(reverse("reception"), {"lang": "en"})
        self.assertEqual(english.context["language"], "en")
        csrf = client.cookies["csrftoken"].value
        path = reverse("reception-message")
        payload = {"language": "pl", "start": True}
        self.assertEqual(client.post(path, payload, content_type="application/json").status_code, 403)
        response = client.post(path, payload, content_type="application/json", headers={"x-csrftoken": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Dzień dobry", response.json()["reply"])

    def test_existing_quote_can_finish_after_upgrade(self):
        quote = self.quote()
        conversation = Conversation.objects.get()
        conversation.state.pop("flow")
        conversation.state.update(name="Ada Example", email="ada@example.com")
        conversation.save(update_fields=["state"])
        self.turn(quote, "Yes", "completed")
        self.assertEqual(Booking.objects.count(), 1)

    @patch("reception.views.extract_stay_value")
    def test_english_switch_preserves_the_browser_session(self, extract):
        opening = self.post(language="pl", start=True).json()
        session = self.client.session.session_key
        english = self.turn(opening, "2", "check_in", voice=True)
        self.assertEqual(english["language"], "en")
        self.assertEqual(self.client.session.session_key, session)
        self.assertEqual(self.client.get(reverse("reception")).context["language"], "en")
        self.assertIn("planned arrival date", english["reply"])
        self.turn(english, "tomorrow", "check_out", voice=True)
        extract.assert_not_called()

    def test_voice_room_options_are_limited_to_two(self):
        third = Room.objects.create(code="ROOM-8", capacity=8)
        Price.objects.create(room=third, nightly_rate=Decimal("250"))
        for flags in ({}, {"voice": True}):
            with self.subTest(flags=flags):
                current = self.post(language="en", reset=True).json()
                current = self.turn(current, "tomorrow", "check_out", **flags)
                current = self.turn(current, "for two nights", "guests", **flags)
                current = self.turn(current, "2", "room", **flags)
                self.assertEqual("ROOM-8" in current["reply"], not flags)

    def test_rejects_invalid_protocol_data(self):
        self.assertEqual(self.post(language="de", start=True).status_code, 400)
        self.assertEqual(self.post(language="en", message="Yes").status_code, 400)
        self.assertEqual(self.post(language="en", reset=True, start=True).status_code, 400)


class ReceptionHandoverTests(TestCase):
    def post(self, **payload):
        return self.client.post(reverse("reception-message"), {"language": "en", **payload}, content_type="application/json")

    def test_non_booking_inquiry_routes_to_staff_and_back_to_its_guest(self):
        opening = self.post(start=True).json()
        forwarded = self.post(revision=opening["revision"], message="I'd like to ask about breakfast.").json()
        self.assertEqual(forwarded["stage"], "handover")
        first = forwarded["handover_messages"][0]
        self.assertFalse(first["staff"])
        self.assertIn("breakfast", first["text"])

        staff = Client()
        staff.force_login(get_user_model().objects.create_superuser(username="reception-staff"))
        path = reverse("admin:reception_staffhandover_change", args=[StaffHandover.objects.get().pk])
        self.assertContains(staff.get(path), escape(first["text"]))
        self.assertRedirects(staff.post(path, {
            "reply": "Breakfast begins at seven.",
            "closed": "on",
            "messages-TOTAL_FORMS": "1",
            "messages-INITIAL_FORMS": "1",
            "messages-MIN_NUM_FORMS": "0",
            "messages-MAX_NUM_FORMS": "0",
            "messages-0-id": str(first["id"]),
        }), reverse("admin:reception_staffhandover_changelist"))

        poll = {"poll": True, "revision": forwarded["revision"], "cursor": first["id"]}
        response = self.post(**poll).json()
        self.assertTrue(response["handover_closed"])
        self.assertEqual(len(response["handover_messages"]), 1)
        reply = response["handover_messages"][0]
        self.assertTrue(reply["staff"])
        self.assertEqual(reply["text"], "Breakfast begins at seven.")
        self.assertEqual(self.post(**{**poll, "cursor": reply["id"]}).json()["handover_messages"], [])
        self.assertEqual(self.post(revision=forwarded["revision"], message="A new question").status_code, 409)
        self.assertEqual(StaffHandover.objects.get().messages.count(), 2)
        self.assertEqual(Client().post(
            reverse("reception-message"), {"language": "en", **poll}, content_type="application/json",
        ).status_code, 404)

    def test_timeout_and_guest_followup_are_revision_guarded(self):
        opening = self.post(start=True).json()
        departure = self.post(revision=opening["revision"], message="tomorrow")
        self.assertEqual(departure.json()["stage"], "check_out")
        self.assertEqual(self.post(revision=opening["revision"], timeout=True).status_code, 409)
        self.assertFalse(StaffHandover.objects.exists())

        opening = self.post(reset=True).json()
        forwarded = self.post(revision=opening["revision"], timeout=True, voice=True).json()
        self.assertEqual(forwarded["stage"], "handover")
        self.assertEqual(forwarded["handover_messages"], [])
        message = {"revision": forwarded["revision"], "message": "Could someone help?"}
        self.assertEqual(self.post(**message).status_code, 200)
        self.assertEqual(self.post(**message).status_code, 409)
        self.assertEqual(StaffHandover.objects.get().messages.count(), 1)
