from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from bookings.models import Booking, BookingConfirmation, Client as Guest, Price, Room
from bookings.services import confirm_booking

from .models import Conversation, ScenarioPrompt, StaffHandover
from .parsing import parse_stay_input


class StayInputTests(TestCase):
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
        self.assertFalse(Booking.objects.exists())

        ambiguous = self.turn(quote, "Yes, maybe", "confirm")
        self.assertEqual(ambiguous["reply"], quote["reply"])
        self.assertFalse(Booking.objects.exists())

        delivery = self.turn(ambiguous, "Yes.", "delivery")
        self.assertTrue(delivery["reply"].startswith("Your reservation has been tentatively created!"))
        email = self.turn(delivery, "Email", "email")
        email = self.turn(email, "invalid@example", "email")
        self.assertFalse(Booking.objects.exists())
        pending = self.turn(email, "a d a at example dot com", "contact_confirm", voice=True)
        self.assertEqual(pending["contact_candidate"], "ada@example.com")
        self.assertIn("a, d, a, at sign", pending["voice_parts"][0]["text"])
        self.assertFalse(BookingConfirmation.objects.exists())
        confirmed = self.turn(pending, "yes", "completed")
        self.assertIn(confirmed["reference"], confirmed["reply"])
        self.assertEqual(str(Booking.objects.get().reference), confirmed["reference"])
        self.assertNotIn("email", Conversation.objects.get().state)
        self.assertEqual(BookingConfirmation.objects.get().method, "email")
        self.assertEqual(Booking.objects.get().client.email, "ada@example.com")
        self.assertEqual(Booking.objects.get().total_price, Decimal("240"))

        retry = self.post(language="en", revision=pending["revision"], message="yes")
        self.assertEqual(retry.status_code, 409)
        self.assertTrue(retry.json()["stale"])
        self.assertEqual(retry.json()["reference"], confirmed["reference"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_polish_quote_and_explicit_confirmation(self):
        quote = self.quote("pl", guests="sześć osób")
        self.assertIn("ROOM-6", quote["reply"])
        quote = self.turn(quote, "Yes", "confirm")
        self.assertFalse(Booking.objects.exists())
        delivery = self.turn(quote, "Potwierdzam", "delivery")
        self.assertTrue(delivery["reply"].startswith("Rezerwacja została wstępnie utworzona!"))
        phone = self.turn(delivery, "SMS", "phone")
        phone = self.turn(phone, "123", "phone")
        self.assertFalse(Booking.objects.exists())
        pending = self.turn(phone, "500 000 123", "contact_confirm")
        self.assertEqual(pending["contact_candidate"], "+48500000123")
        self.assertFalse(Booking.objects.exists())
        confirmed = self.turn(pending, "tak", "completed")
        self.assertIn("Twoja rezerwacja została w pełni potwierdzona", confirmed["reply"])
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

    @patch("reception.views.extract_stay_value")
    @patch("django.utils.timezone.localdate", return_value=date(2027, 12, 30))
    def test_noisy_stay_collects_both_dates_and_prices_three_nights(self, _today, extract):
        for language, voice, phrase, guests, displayed_dates, total in (
            ("en", False, "strike of will be the from tomorrow for three days", "two guests",
             ("2027-12-31", "2028-01-03"), "360.00 PLN"),
            ("pl", True, "trzask będzie chyba od jutra na trzy dni", "dwie osoby",
             ("31.12.2027", "03.01.2028"), "360,00 PLN"),
        ):
            with self.subTest(language=language):
                opening = self.post(language=language, reset=True).json()
                current = self.turn(opening, phrase, "guests", **({"voice": True} if voice else {}))
                for displayed_date in displayed_dates:
                    self.assertIn(displayed_date, current["reply"])
                state = Conversation.objects.get().state
                self.assertEqual((state["check_in"], state["check_out"]), ("2027-12-31", "2028-01-03"))
                offers = self.turn(current, guests, "room")
                self.assertIn(total, offers["reply"])
        extract.assert_not_called()
        self.assertFalse(Booking.objects.exists())

    @patch("django.utils.timezone.localdate", return_value=date(2027, 12, 30))
    def test_checkout_relative_to_today_after_a_separate_arrival(self, today):
        for language, arrival, departure, question in (
            (
                "en", "tomorrow", "for two nights", "number of nights",
            ),
            (
                "pl", "jutro", "na dwie noce", "na ile nocy",
            ),
        ):
            with self.subTest(language=language):
                current = self.post(language=language, reset=True).json()
                current = self.turn(current, arrival, "check_out")
                self.assertIn(question, current["reply"])
                current = self.turn(current, arrival, "check_out")
                self.turn(current, departure, "guests")
                state = Conversation.objects.get().state
                self.assertEqual(state["check_in"], "2027-12-31")
                self.assertEqual(state["check_out"], "2028-01-02")

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
        delivery = self.turn(quote, "Confirm", "delivery")
        email = self.turn(delivery, "Email", "email")
        pending = self.turn(email, "ada@example.com", "contact_confirm")
        Price.objects.filter(room=self.double_room).update(nightly_rate=Decimal("130"))
        current = self.turn(pending, "yes", "check_in")
        self.assertIn("fresh quote", current["reply"])
        self.assertFalse(Booking.objects.exists())

    def test_contact_readback_rejects_uncertain_and_stale_approval(self):
        delivery = self.turn(self.quote(), "yes", "delivery")
        email = self.turn(delivery, "email", "email")
        first = self.turn(email, "guess at example dot com", "contact_confirm", voice=True)
        self.assertIn("guess@example.com", first["reply"])
        self.assertFalse(Guest.objects.exists())
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(BookingConfirmation.objects.exists())

        maybe = self.turn(first, "yes, maybe", "contact_confirm")
        self.assertEqual(maybe["contact_candidate"], "guess@example.com")
        replacement = self.turn(maybe, "guest@example.com", "contact_confirm")
        self.assertNotEqual(replacement["revision"], maybe["revision"])
        stale = self.post(language="en", revision=maybe["revision"], message="yes")
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(Booking.objects.exists())

        cleared = self.turn(replacement, "no", "email")
        self.assertNotIn("contact_candidate", cleared)
        self.assertNotIn("contact_candidate", Conversation.objects.get().state)
        cleared = self.turn(cleared, "yes", "email")
        pending = self.turn(cleared, "guest@example.com", "contact_confirm")
        completed = self.turn(pending, "yes", "completed")
        self.assertNotIn("guest@example.com", completed["reply"])
        self.assertEqual(BookingConfirmation.objects.get().destination, "guest@example.com")

    def test_delivery_change_requires_fresh_contact_and_approval(self):
        delivery = self.turn(self.quote("pl", guests="dwie osoby"), "tak", "delivery")
        phone = self.turn(delivery, "sms", "phone")
        pending = self.turn(phone, "plus cztery osiem pięć zero jeden dwa trzy cztery pięć sześć osiem", "contact_confirm")
        self.assertEqual(pending["contact_candidate"], "+48501234568")
        self.assertIn("plus, cztery, osiem, pięć", pending["voice_parts"][0]["text"])
        delivery = self.turn(pending, "zmień sposób dostawy", "delivery")
        self.assertNotIn("contact_candidate", Conversation.objects.get().state)
        email = self.turn(delivery, "email", "email")
        pending = self.turn(email, "gosc@example.test", "contact_confirm")
        self.assertFalse(Booking.objects.exists())
        self.turn(pending, "tak", "completed")
        self.assertEqual(BookingConfirmation.objects.get().destination, "gosc@example.test")

    def test_availability_is_rechecked_when_contact_is_approved(self):
        delivery = self.turn(self.quote(), "yes", "delivery")
        email = self.turn(delivery, "email", "email")
        pending = self.turn(email, "guest@example.test", "contact_confirm")
        state = Conversation.objects.get().state
        confirm_booking(
            room_id=state["room_id"], check_in=date.fromisoformat(state["check_in"]),
            check_out=date.fromisoformat(state["check_out"]), guests=state["guests"],
            name="Fixture", email="fixture@example.test", nightly_rate=Decimal(state["nightly_rate"]),
            idempotency_key=uuid4(),
        )
        self.turn(pending, "yes", "check_in")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertFalse(BookingConfirmation.objects.exists())

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
        conversation.state.update(name="Ada Example", email="ada@example.com", booking_key=str(uuid4()))
        conversation.save(update_fields=["state"])
        pending = self.turn(quote, "Yes", "contact_confirm")
        self.assertFalse(pending["can_change_delivery"])
        self.assertFalse(Booking.objects.exists())
        self.turn(pending, "Yes", "completed")
        self.assertEqual(Booking.objects.count(), 1)

    def test_legacy_contact_readback_returns_to_original_quote_without_entry_overrides(self):
        quote = self.quote()
        conversation = Conversation.objects.get()
        conversation.state.pop("flow")
        conversation.state.update(stage="email", name="Ada Example", booking_key=str(uuid4()))
        conversation.save(update_fields=["state"])
        ScenarioPrompt.objects.filter(language="en", key="email").update(chat_text="Custom entry text")
        candidate = self.turn(quote, "ada@example.com", "contact_confirm")
        revised_quote = self.turn(candidate, "yes", "confirm")
        self.assertIn("Reply Yes to book", revised_quote["reply"])
        self.assertNotIn("contact_candidate", Conversation.objects.get().state)
        self.assertFalse(Booking.objects.exists())
        self.turn(revised_quote, "yes", "completed")

    def test_english_page_starts_and_restarts_at_arrival(self):
        page = self.client.get(reverse("reception"), {"lang": "en"})
        language = page.context["language"]
        opening = self.post(language=language, start=True).json()
        self.assertEqual(opening["stage"], "check_in")
        self.assertTrue(opening["reply"].startswith("Hi, this is Neptun Resort, Pucka 2 street"))
        self.assertIn("Specify the dates of your stay", opening["reply"])
        self.assertNotIn("welcome_parts", opening)
        self.assertNotIn("voice_welcome", opening)
        self.turn(opening, "tomorrow", "check_out", voice=True)
        restarted = self.post(language=language, reset=True).json()
        self.assertEqual(restarted["stage"], "check_in")
        self.assertEqual(restarted["reply"], opening["reply"])
        self.assertEqual(self.post(language=language, start=True).json(), restarted)

    def test_english_arrival_saved_without_a_greeting_is_updated_on_start(self):
        opening = self.post(language="en", start=True).json()
        prior = Conversation.objects.get()
        prior.state["reply"] = prior.state["reply"].removeprefix("Hi, this is Neptun Resort Jastrzębia Góra. ")
        prior.save(update_fields=["state"])
        refreshed = self.post(language="en", start=True).json()
        self.assertEqual(refreshed["reply"], opening["reply"])
        self.assertNotEqual(refreshed["revision"], prior.state["revision"])

    def test_selecting_english_on_the_page_skips_the_polish_welcome(self):
        polish = self.post(language="pl", start=True).json()
        self.assertEqual(polish["stage"], "welcome")
        self.assertEqual(self.post(language="en", start=True).json()["stage"], "check_in")

    def test_start_skips_an_english_welcome_saved_before_the_update(self):
        self.post(language="en", start=True)
        prior = Conversation.objects.get()
        prior.state.update(stage="welcome")
        prior.save(update_fields=["state"])
        started = self.post(language="en", start=True).json()
        self.assertEqual(started["stage"], "check_in")
        self.assertNotEqual(started["revision"], prior.state["revision"])

    @patch("reception.views.extract_stay_value")
    def test_english_switch_preserves_the_browser_session(self, extract):
        opening = self.post(language="pl", start=True).json()
        self.assertEqual(opening["stage"], "welcome")
        session = self.client.session.session_key
        english = self.turn(opening, "2", "check_in", voice=True)
        self.assertEqual(english["language"], "en")
        self.assertEqual(self.client.session.session_key, session)
        self.assertEqual(self.client.get(reverse("reception")).context["language"], "en")
        self.assertIn("Specify the dates of your stay", english["reply"])
        self.turn(english, "tomorrow", "check_out", voice=True)
        extract.assert_not_called()

    def test_bilingual_suggestions_submit_valid_dates_durations_and_rooms(self):
        for language, tomorrow, nights in (("en", "Tomorrow", "2 nights"), ("pl", "Jutro", "2 noce")):
            with self.subTest(language=language):
                current = self.post(language=language, reset=True).json()
                if language == "pl":
                    current = self.post(language=language, revision=current["revision"], timeout=True).json()
                self.assertIn({"label": tomorrow, "text": tomorrow}, current["suggestions"])
                current = self.turn(current, tomorrow, "check_out")
                self.assertIn({"label": nights, "text": nights}, current["suggestions"])
                current = self.turn(current, nights, "guests")
                current = self.turn(current, current["suggestions"][1]["text"], "room")
                self.assertEqual(current["suggestions"][0], {"label": "ROOM-2", "text": "1"})
                current = self.turn(current, current["suggestions"][0]["text"], "confirm")
                self.assertNotIn("suggestions", current)

    def test_timed_welcome_defaults_to_polish_and_voice_can_replay_it(self):
        opening = self.post(language="pl", start=True).json()
        self.assertEqual(opening["stage"], "welcome")
        self.assertEqual([part[0] for part in opening["welcome_parts"]], ["pl", "en", "pl"])
        self.assertNotIn("termin przyjazdu", opening["reply"])
        timed_out = self.post(language="pl", revision=opening["revision"], timeout=True)
        self.assertEqual(timed_out.status_code, 200)
        arrival = timed_out.json()
        self.assertEqual(arrival["stage"], "check_in")
        self.assertIn("termin przyjazdu", arrival["reply"])
        self.assertTrue(arrival["voice_welcome"])
        self.assertFalse(StaffHandover.objects.exists())
        self.assertEqual(
            self.post(language="pl", revision=opening["revision"], timeout=True).status_code, 409,
        )
        voice = self.post(language="pl", revision=arrival["revision"], voice_start=True).json()
        self.assertEqual(voice["stage"], "welcome")
        checkout = self.turn(voice, "jutro", "check_out", voice=True)
        self.assertEqual(self.post(language="pl", revision=checkout["revision"], voice_start=True).json(), checkout)

    def test_start_replaces_pre_upgrade_initial_greeting(self):
        for language, old_reply in (
            ("en", "Welcome to Resort Neptun Jastrzębia Góra. Please give your arrival date."),
            ("en", "To check availability, prices, or make a reservation, please give your planned arrival date. For anything else, ask for reception."),
            ("pl", "Aby sprawdzić dostępność, ceny lub dokonać rezerwacji, podaj planowaną datę przyjazdu. W innej sprawie poproś o kontakt z recepcją."),
        ):
            with self.subTest(language=language, old_reply=old_reply):
                self.post(language=language, start=True)
                prior = Conversation.objects.get()
                prior.state.update(stage="check_in", reply=old_reply)
                prior.save(update_fields=["state"])
                started = self.post(language=language, start=True).json()
                self.assertEqual(started["stage"], "check_in" if language == "en" else "welcome")
                self.assertEqual(started["language"], language)

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
    def test_previous_model_can_create_handovers_on_the_current_schema(self):
        previous = MigrationExecutor(connection).loader.project_state([
            ("reception", "0012_staff_handover_assignee"),
        ]).apps
        conversation = Conversation.objects.create(conversation_id=uuid4().hex, state={})
        handover = previous.get_model("reception", "StaffHandover").objects.create(
            conversation_id=conversation.pk, language="en",
        )
        self.assertEqual(StaffHandover.objects.get(pk=handover.pk).last_read_guest_message_id, 0)

    def test_admin_marks_only_the_displayed_guest_messages_read(self):
        operator = get_user_model().objects.create_superuser(username="unread-operator")
        self.client.force_login(operator)
        handover, other = [
            StaffHandover.objects.create(
                conversation=Conversation.objects.create(conversation_id=uuid4().hex, state={}), language="en",
            )
            for _ in range(2)
        ]
        handover.assignee = operator
        handover.save(update_fields=["assignee"])
        guest = handover.messages.create(text="Fictional first guest message.")
        reply = handover.messages.create(staff=True, text="Fictional staff reply.")
        other.messages.create(text="Fictional separate message.")
        detail = reverse("admin:reception_staffhandover_change", args=[handover.pk])
        endpoint = reverse("admin:reception_staffhandover_mark_read", args=[handover.pk])
        other_endpoint = reverse("admin:reception_staffhandover_mark_read", args=[other.pk])
        inbox = reverse("admin:reception_staffhandover_changelist")
        initial = self.client.get(detail)
        self.assertContains(initial, "Mark read")
        self.assertContains(initial, guest.text)
        snapshot = initial.context["read_snapshot"]
        later = handover.messages.create(text="Fictional later guest message.")

        self.assertEqual(self.client.get(endpoint).status_code, 405)
        for invalid_endpoint, invalid_snapshot in ((endpoint, snapshot + "-tampered"), (other_endpoint, snapshot)):
            self.assertEqual(self.client.post(invalid_endpoint, {"snapshot": invalid_snapshot}).status_code, 400)
        self.assertRedirects(self.client.post(endpoint, {"snapshot": snapshot}), detail)
        handover.refresh_from_db()
        self.assertEqual(handover.last_read_guest_message_id, guest.pk)
        request = RequestFactory().get(inbox)
        request.user = operator
        inbox_admin = admin.site.get_model_admin(StaffHandover)
        with self.assertNumQueries(1):
            self.assertEqual(
                {row.pk: inbox_admin.unread_count(row) for row in inbox_admin.get_queryset(request)},
                {handover.pk: 1, other.pk: 1},
            )
        unread_filter = self.client.get(inbox, {"unread": "yes"})
        self.assertContains(unread_filter, "Has unread messages")
        self.assertCountEqual(unread_filter.context["cl"].result_list, [handover, other])
        self.assertCountEqual(
            self.client.get(inbox, {
                "unread": "yes", "first_response": "replied", "language__exact": "en",
                "closed__exact": "0", "assignee__id__exact": operator.pk,
            }).context["cl"].result_list,
            [handover],
        )
        latest_snapshot = self.client.get(detail).context["read_snapshot"]
        self.assertRedirects(self.client.post(endpoint, {"snapshot": latest_snapshot}), detail)
        self.assertRedirects(self.client.post(endpoint, {"snapshot": snapshot}), detail)
        self.assertNotContains(self.client.get(detail), "Mark read")
        self.assertCountEqual(self.client.get(inbox, {"unread": "yes"}).context["cl"].result_list, [other])
        self.assertCountEqual(self.client.get(inbox, {"unread": "no"}).context["cl"].result_list, [handover])
        reply_response = self.client.post(detail, {
            "messages-TOTAL_FORMS": "3", "messages-INITIAL_FORMS": "3", "reply": "Fictional follow-up.",
            "messages-0-id": guest.pk, "messages-1-id": reply.pk, "messages-2-id": later.pk,
        })
        self.assertRedirects(reply_response, inbox)
        handover.refresh_from_db()
        self.assertEqual(handover.last_read_guest_message_id, later.pk)
        self.assertEqual(handover.messages.filter(staff=True).count(), 2)

        viewer = get_user_model().objects.create_user(username="unread-viewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="reception", codename="view_staffhandover",
        ))
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(endpoint, {"snapshot": latest_snapshot}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(operator)
        self.assertEqual(csrf_client.post(endpoint, {"snapshot": latest_snapshot}).status_code, 403)

    def test_admin_assignment_validates_permissions_and_preserves_existing_assignee(self):
        users = get_user_model().objects
        operator = users.create_superuser(username="assignment-operator")
        receptionist = users.create_user(username="assignment-receptionist", is_staff=True)
        viewer = users.create_user(username="assignment-viewer", is_staff=True)
        nonstaff = users.create_user(username="assignment-nonstaff")
        group = Group.objects.create(name="Assignment reception team")
        change_permission = Permission.objects.get(
            content_type__app_label="reception", codename="change_staffhandover",
        )
        group.permissions.add(change_permission)
        receptionist.groups.add(group)
        nonstaff.user_permissions.add(change_permission)
        viewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="reception", codename="view_staffhandover",
        ))
        handover = StaffHandover.objects.create(
            conversation=Conversation.objects.create(conversation_id=uuid4().hex, state={}), language="en",
        )
        detail_path = reverse("admin:reception_staffhandover_change", args=[handover.pk])
        inbox_path = reverse("admin:reception_staffhandover_changelist")
        forms = {"messages-TOTAL_FORMS": "0", "messages-INITIAL_FORMS": "0"}
        self.client.force_login(operator)
        for invalid_user in (viewer, nonstaff):
            with self.subTest(user=invalid_user):
                rejected = self.client.post(detail_path, {**forms, "assignee": invalid_user.pk})
                self.assertIn("assignee", rejected.context["adminform"].form.errors)
                handover.refresh_from_db()
                self.assertIsNone(handover.assignee_id)

        self.assertRedirects(self.client.post(detail_path, {**forms, "assignee": receptionist.pk}), inbox_path)
        handover.refresh_from_db()
        self.assertEqual(handover.assignee, receptionist)
        filtered = self.client.get(inbox_path, {"assignee__id__exact": receptionist.pk})
        self.assertContains(filtered, receptionist.username)
        self.assertCountEqual(filtered.context["cl"].result_list, [handover])
        self.assertCountEqual(self.client.get(inbox_path, {"assignee__isnull": 1}).context["cl"].result_list, [])

        receptionist.is_active = False
        receptionist.save(update_fields=["is_active"])
        self.assertRedirects(self.client.post(detail_path, forms), inbox_path)
        handover.refresh_from_db()
        self.assertEqual(handover.assignee, receptionist)
        self.assertRedirects(self.client.post(detail_path, {
            **forms, "assignee": receptionist.pk, "reply": "Fictional staff follow-up.",
        }), inbox_path)
        handover.refresh_from_db()
        self.assertEqual(handover.assignee, receptionist)
        self.assertEqual(handover.messages.get().text, "Fictional staff follow-up.")
        self.assertRedirects(self.client.post(detail_path, {**forms, "assignee": ""}), inbox_path)
        handover.refresh_from_db()
        self.assertIsNone(handover.assignee_id)
        rejected = self.client.post(detail_path, {**forms, "assignee": receptionist.pk})
        self.assertIn("assignee", rejected.context["adminform"].form.errors)
        self.client.force_login(viewer)
        self.assertContains(self.client.get(detail_path), "Assignee")
        self.assertEqual(self.client.post(detail_path, {**forms, "assignee": operator.pk}).status_code, 403)

    def test_admin_shows_first_staff_response_and_unanswered_states(self):
        now = timezone.now()
        waiting, closed, answered = [
            StaffHandover.objects.create(
                conversation=Conversation.objects.create(conversation_id=uuid4().hex, state={}),
                language="en",
                closed=is_closed,
            )
            for is_closed in (False, True, True)
        ]
        StaffHandover.objects.filter(pk__in=(waiting.pk, closed.pk, answered.pk)).update(created_at=now)
        waiting.messages.create(text="Is anyone available?")
        answer_times = (
            (False, 1),
            (True, 240),
            (True, 90),
        )
        for staff, seconds in answer_times:
            message = answered.messages.create(staff=staff, text="Fictional handover message.")
            answered.messages.filter(pk=message.pk).update(created_at=now + timedelta(seconds=seconds))

        operator = get_user_model().objects.create_superuser(username="handover-reviewer")
        self.client.force_login(operator)
        inbox_path = reverse("admin:reception_staffhandover_changelist")
        inbox = self.client.get(inbox_path)
        for label in ("First staff response", "Awaiting reply", "Closed without reply", "0:01:30"):
            self.assertContains(inbox, label)
        detail = self.client.get(reverse("admin:reception_staffhandover_change", args=[answered.pk]))
        self.assertContains(detail, "0:01:30")

        request = RequestFactory().get(inbox_path)
        request.user = operator
        inbox_admin = admin.site.get_model_admin(StaffHandover)
        with self.assertNumQueries(1):
            self.assertEqual(
                {row.pk: inbox_admin.first_staff_response(row) for row in inbox_admin.get_queryset(request)},
                {waiting.pk: "Awaiting reply", closed.pk: "Closed without reply", answered.pk: "0:01:30"},
            )

    def test_admin_filter_combines_first_staff_response_with_language_and_closed(self):
        waiting_en, waiting_pl, replied_open, replied_closed, closed_unanswered = [
            StaffHandover.objects.create(
                conversation=Conversation.objects.create(conversation_id=uuid4().hex, state={}),
                language=language,
                closed=closed,
            )
            for language, closed in (("en", False), ("pl", False), ("en", False), ("pl", True), ("en", True))
        ]
        waiting_en.messages.create(text="Fictional guest message.")
        replied_open.messages.create(staff=True, text="Fictional first reply.")
        replied_open.messages.create(staff=True, text="Fictional second reply.")
        replied_closed.messages.create(staff=True, text="Fictional staff reply.")

        operator = get_user_model().objects.create_superuser(username="handover-filter-operator")
        self.client.force_login(operator)
        inbox_path = reverse("admin:reception_staffhandover_changelist")
        cases = (
            ({"first_response": "awaiting"}, [waiting_en, waiting_pl]),
            ({"first_response": "replied"}, [replied_open, replied_closed]),
            ({"first_response": "closed_without_reply"}, [closed_unanswered]),
            ({"first_response": "awaiting", "language__exact": "pl"}, [waiting_pl]),
            ({"first_response": "replied", "closed__exact": "1"}, [replied_closed]),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get(inbox_path, filters)
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(response.context["cl"].result_list, expected)

    def post(self, **payload):
        return self.client.post(reverse("reception-message"), {"language": "en", **payload}, content_type="application/json")

    def test_requested_voice_handover_is_bilingual_revision_guarded_and_resumable(self):
        for language, reply in (
            ("en", "You can leave a message for reception staff here."),
            ("pl", "Możesz tutaj zostawić wiadomość dla pracownika recepcji."),
        ):
            with self.subTest(language=language):
                self.client = Client()
                opening = self.post(start=True, voice=True, language=language).json()
                request = {"revision": opening["revision"], "handover": True, "voice": True, "language": language}
                self.assertTrue(opening["can_request_handover"])
                self.assertEqual(self.post(**{**request, "voice": False}).status_code, 400)
                self.assertEqual(self.post(**request, message="Unsent voice answer").status_code, 400)
                current = self.post(**request).json()
                self.assertEqual(current["stage"], "handover")
                self.assertEqual(current["reply"], reply)
                self.assertFalse(current["voice_welcome"])
                self.assertNotIn("can_request_handover", current)
                self.assertEqual(current["handover_messages"], [])
                self.assertEqual(self.post(**request).status_code, 409)
                request["revision"] = current["revision"]
                self.assertEqual(self.post(**request).status_code, 409)
                self.assertEqual(self.post(start=True, resume=True, language=language).json(), current)
                legacy = self.post(voice_start=True, revision=current["revision"], language=language).json()
                self.assertEqual(legacy, current)
                conversation = Conversation.objects.get(conversation_id=self.client.session.session_key)
                handover = StaffHandover.objects.get(conversation=conversation, language=language, closed=False)
                self.assertEqual(conversation.state["scenario_pin"]["channel"], "voice")
                followup = self.post(revision=current["revision"], language=language, message="Please help.")
                self.assertEqual(followup.status_code, 200)
                self.assertEqual(list(handover.messages.values_list("text", flat=True)), ["Please help."])

    def test_request_voice_handover_is_unavailable_for_protected_stages(self):
        for stage in ("delivery_pending", "completed", "cancelled"):
            with self.subTest(stage=stage):
                self.client = Client()
                opening = self.post(start=True, voice=True).json()
                conversation = Conversation.objects.get(conversation_id=self.client.session.session_key)
                conversation.state.update(stage=stage, reference="SAMPLE-REFERENCE")
                conversation.save(update_fields=["state"])
                response = self.post(revision=opening["revision"], handover=True, voice=True)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["stage"], stage)
                self.assertNotIn("can_request_handover", response.json())
                self.assertFalse(StaffHandover.objects.filter(conversation=conversation).exists())

    def test_only_dedicated_voice_shows_receptionist_control(self):
        self.assertContains(self.client.get("/voice/?lang=en"), "Message reception")
        self.assertContains(self.client.get("/voice/?lang=pl"), "Napisz do recepcji")
        for path in ("/", "/chat/"):
            self.assertNotContains(self.client.get(path), 'id="request-receptionist"')

    def test_non_booking_inquiry_routes_to_staff_and_back_to_its_guest(self):
        opening = self.post(start=True).json()
        arrival = self.post(revision=opening["revision"], timeout=True).json()
        forwarded = self.post(revision=arrival["revision"], message="I'd like to ask about breakfast.").json()
        self.assertEqual(forwarded["stage"], "handover")
        self.assertFalse(forwarded["voice_welcome"])
        first = forwarded["handover_messages"][0]
        self.assertFalse(first["staff"])
        self.assertIn("breakfast", first["text"])

        staff = Client()
        receptionist = get_user_model().objects.create_superuser(username="reception-staff")
        staff.force_login(receptionist)
        path = reverse("admin:reception_staffhandover_change", args=[StaffHandover.objects.get().pk])
        self.assertContains(staff.get(path), escape(first["text"]))
        self.assertRedirects(staff.post(path, {
            "reply": "Breakfast begins at seven.",
            "assignee": receptionist.pk,
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
        self.assertNotIn("assignee", response)
        self.assertEqual(StaffHandover.objects.get().assignee, receptionist)
        self.assertEqual(len(response["handover_messages"]), 1)
        reply = response["handover_messages"][0]
        self.assertTrue(reply["staff"])
        self.assertEqual(reply["text"], "Breakfast begins at seven.")
        self.assertNotIn(receptionist.username, str(reply))
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

        arrival = self.post(reset=True).json()
        forwarded = self.post(revision=arrival["revision"], timeout=True, voice=True).json()
        self.assertEqual(forwarded["stage"], "handover")
        self.assertEqual(forwarded["handover_messages"], [])
        message = {"revision": forwarded["revision"], "message": "Could someone help?"}
        self.assertEqual(self.post(**message).status_code, 200)
        self.assertEqual(self.post(**message).status_code, 409)
        self.assertEqual(StaffHandover.objects.get().messages.count(), 1)
        current = self.post(start=True).json()
        self.assertEqual(self.post(revision=current["revision"], voice_start=True).json()["stage"], "handover")

    def test_voice_can_start_after_empty_silence_handover(self):
        arrival = self.post(start=True).json()
        forwarded = self.post(revision=arrival["revision"], timeout=True).json()
        self.assertTrue(forwarded["voice_welcome"])
        voice = self.post(revision=forwarded["revision"], voice_start=True).json()
        self.assertEqual(voice["stage"], "check_in")
        self.assertTrue(StaffHandover.objects.get().closed)
