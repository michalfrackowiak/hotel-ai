from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from bookings.models import Booking, BookingConfirmation, Price, Room

from .models import Conversation, ParserSample, Scenario, ScenarioTransition, StaffHandover
from .scenario_publication import publish_scenario_version, review_scenario_selection
from .scenario_validation import APPROVED_STEPS, APPROVED_TRANSITIONS


@patch("django.utils.timezone.localdate", return_value=date(2027, 1, 1))
class BookingDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for index in range(1, 4):
            room = Room.objects.create(code=f"ROOM-{index}", capacity=2)
            Price.objects.create(room=room, nightly_rate=index * 100)

    def post(self, language="en", **payload):
        return self.client.post(reverse("reception-message"), {"language": language, **payload},
                                content_type="application/json")

    def advance(self, turn, **payload):
        response = self.post(turn["language"], revision=turn["revision"], **payload)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def start(self, language="en", **flags):
        return self.post(language, reset=True, **flags).json()

    def stay(self, turn):
        return self.advance(turn, dates={"check_in": "2027-01-02", "check_out": "2027-01-05"})

    def test_controls_are_on_the_dedicated_pages(self, _today):
        for mode in ("chat", "voice"):
            page = self.client.get(reverse(f"reception-{mode}"))
            self.assertContains(page, 'id="booking-date-picker"')
            self.assertContains(page, 'id="booking-summary"')
        self.assertNotContains(self.client.get("/"), 'id="booking-date-picker"')

    @override_settings(HOTEL_PARSER_TRAINING_ENABLED=True)
    def test_date_selection_is_strict_and_does_not_enter_training(self, _today):
        first = self.start()
        self.assertEqual(first["date_picker"]["arrival_min"], "2027-01-01")
        for malformed in ({"check_in": "01/02/2027"}, {"check_in": "10000-01-01"},
                          {"check_in": "2027-02-29"}, {"check_in": True},
                          {"check_in": "2027-01-02", "guests": 2}):
            with self.subTest(malformed=malformed):
                self.assertEqual(self.post(revision=first["revision"], dates=malformed).status_code, 400)
        for invalid in ({"check_in": "2026-12-31"}, {"check_in": "9999-12-31"},
                        {"check_in": "2027-01-03", "check_out": "2027-01-02"}):
            with self.subTest(invalid=invalid):
                result = self.advance(first, dates=invalid)
                self.assertTrue(result["date_error"])
                self.assertEqual(result["revision"], first["revision"])

        checkout = self.advance(first, dates={"check_in": "2027-01-01"})
        self.assertEqual(checkout["stage"], "check_out")
        self.assertEqual(checkout["date_picker"], {"departure_min": "2027-01-02", "departure_required": True})
        invalid = self.advance(checkout, dates={"check_out": "2027-01-01"})
        self.assertTrue(invalid["date_error"])
        self.assertEqual(invalid["revision"], checkout["revision"])
        guests = self.advance(checkout, dates={"check_out": "2027-01-02"})
        self.assertEqual(guests["stage"], "guests")
        self.assertEqual(guests["booking_summary"], {
            "check_in": "2027-01-01", "check_out": "2027-01-02", "edits": ["dates"],
        })
        self.assertFalse(ParserSample.objects.exists())

    def test_polish_welcome_uses_the_arrival_range_policy(self, _today):
        staff = get_user_model().objects.create_superuser(username="scenario-reviewer")
        scenario = Scenario.objects.create(language="pl", channel="voice")
        version = scenario.versions.create(number=1)
        steps = {
            key: version.steps.create(
                key=key, configuration={"arrival_input": "require_range"} if key == "check_in" else {},
            )
            for key in sorted(APPROVED_STEPS)
        }
        ScenarioTransition.objects.bulk_create(
            ScenarioTransition(source=steps[source], event=event, target=steps[target])
            for source, outcomes in APPROVED_TRANSITIONS.items()
            for event, target in outcomes.items()
        )
        review = review_scenario_selection(scenario.pk, version.pk, actor=staff, action="publish")
        publish_scenario_version(
            scenario.pk, version.pk, actor=staff, expected_version_id=review.expected_version_id,
            expected_audit_id=review.expected_audit_id, expected_fingerprint=review.fingerprint,
        )
        welcome = self.start("pl", voice=True)
        self.assertEqual(welcome["stage"], "welcome")
        self.assertTrue(welcome["date_picker"]["departure_required"])
        incomplete = self.advance(welcome, dates={"check_in": "2027-01-02"}, voice=True)
        self.assertTrue(incomplete["date_error"])
        self.assertEqual(incomplete["revision"], welcome["revision"])
        guests = self.stay(welcome)
        self.assertEqual(guests["stage"], "guests")
        self.assertEqual(guests["language"], "pl")
        edited = self.advance(guests, edit="dates")
        self.assertTrue(edited["date_picker"]["departure_required"])
        self.assertEqual(Conversation.objects.get().state["scenario_pin"]["version_id"], version.pk)

    def test_room_edit_after_approval_refreshes_offers_and_invalidates_contact(self, _today):
        turn = self.stay(self.start())
        for answer in ("2", "1", "yes", "email", "guest@example.test"):
            turn = self.advance(turn, message=answer)
        self.assertEqual(turn["stage"], "contact_confirm")
        self.assertEqual(turn["booking_summary"], {
            "check_in": "2027-01-02", "check_out": "2027-01-05", "guests": 2,
            "room": "ROOM-1", "total_price": "300.00", "edits": ["dates", "guests", "room"],
        })
        Price.objects.filter(room__code="ROOM-1").update(nightly_rate=150)
        edited = self.advance(turn, edit="room", voice=True)
        self.assertEqual(edited["stage"], "room")
        self.assertEqual(edited["booking_summary"]["edits"], ["dates", "guests"])
        draft = Conversation.objects.get().state
        self.assertEqual(len(draft["choices"]), 2)
        self.assertEqual(draft["scenario_pin"]["channel"], "chat")
        for cleared in ("room_id", "room", "total_price", "booking_key", "contact_candidate", "contact_method"):
            self.assertNotIn(cleared, draft)
        self.assertEqual(self.post(revision=turn["revision"], message="yes").status_code, 409)
        self.assertFalse(Booking.objects.exists())
        quote = self.advance(edited, message="1")
        self.assertEqual(quote["stage"], "confirm")
        self.assertEqual(quote["booking_summary"]["total_price"], "450.00")

    def test_guests_and_dates_rewind_only_their_dependent_selections(self, _today):
        room = self.advance(self.stay(self.start()), message="2")
        guests = self.advance(room, edit="guests")
        self.assertEqual(guests["stage"], "guests")
        self.assertEqual(guests["booking_summary"], {
            "check_in": "2027-01-02", "check_out": "2027-01-05", "edits": ["dates"],
        })
        self.assertNotIn("choices", Conversation.objects.get().state)
        dates = self.advance(guests, edit="dates")
        self.assertEqual(dates["stage"], "check_in")
        self.assertNotIn("booking_summary", dates)
        self.assertIn("date_picker", dates)
        self.assertFalse(Booking.objects.exists())

    @override_settings(HOTEL_CONFIRMATION_WAIT_METHODS=())
    def test_handover_and_completed_bookings_cannot_be_edited(self, _today):
        first = self.start()
        handover = self.advance(first, timeout=True)
        self.assertEqual(handover["stage"], "handover")
        self.assertEqual(self.post(revision=handover["revision"], edit="dates").status_code, 409)
        self.assertEqual(self.post(revision=handover["revision"], dates={
            "check_in": "2027-01-03",
        }).status_code, 409)
        self.assertFalse(StaffHandover.objects.get().messages.exists())
        turn = self.stay(self.start())
        for answer in ("2", "1", "yes", "email", "guest@example.test", "yes"):
            turn = self.advance(turn, message=answer)
        self.assertEqual(turn["stage"], "completed")
        self.assertNotIn("booking_summary", turn)
        self.assertEqual(self.post(revision=turn["revision"], edit="room").status_code, 409)
        self.assertEqual(Booking.objects.count(), 1)

    @override_settings(HOTEL_CONFIRMATION_WAIT_METHODS=("email",))
    def test_delivery_in_flight_cannot_be_edited(self, _today):
        turn = self.stay(self.start())
        for answer in ("2", "1", "yes", "email", "guest@example.test", "yes"):
            turn = self.advance(turn, message=answer)
        self.assertEqual(turn["stage"], "delivery_pending")
        self.assertNotIn("booking_summary", turn)
        blocked = self.post(revision=turn["revision"], edit="dates")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["reference"], turn["reference"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingConfirmation.objects.get().status, BookingConfirmation.Status.PENDING)
