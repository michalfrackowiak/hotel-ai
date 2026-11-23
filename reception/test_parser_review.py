from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Price, Room

from .models import Conversation, ParserSample, Scenario, ScenarioTransition
from .parser_training import validate_correction
from .scenario_publication import publish_scenario_version, review_scenario_selection
from .scenario_validation import APPROVED_STEPS, APPROVED_TRANSITIONS


@override_settings(HOTEL_PARSER_TRAINING_ENABLED=True, HOTEL_CONFIRMATION_WAIT_METHODS=())
class ParserReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        room = Room.objects.create(code="ROOM-2", capacity=2)
        Price.objects.create(room=room, nightly_rate=120)

    def request_turn(self, **payload):
        response = self.client.post(reverse("reception-message"), payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def start(self, language="en", **flags):
        turn = self.request_turn(language=language, reset=True, **flags)
        if turn["stage"] == "welcome":
            turn = self.request_turn(language=language, revision=turn["revision"], timeout=True, **flags)
        return turn

    def answer(self, turn, text, **flags):
        return self.request_turn(language=turn["language"], revision=turn["revision"], message=text, **flags)

    def date_steps(self, language="en"):
        turn = self.start(language)
        turn = self.answer(turn, "tomorrow" if language == "en" else "jutro")
        return self.answer(turn, "for a week" if language == "en" else "na tydzień")

    def test_record_bilingual_chat_and_voice_with_extracted_values(self):
        en = self.start()
        en = self.answer(en, "tomorrow", voice=True)  # Typed while microphone mode is active.
        self.answer(en, "a weak", voice=True, transcript=True)
        pl = self.date_steps("pl")
        self.answer(pl, "dwie osoby")
        self.assertEqual(
            list(ParserSample.objects.order_by("pk").values_list("language", "stage", "source", "succeeded")),
            [
                ("en", "check_in", "chat", True),
                ("en", "check_out", "voice", False),
                ("pl", "check_in", "chat", True),
                ("pl", "check_out", "chat", True),
                ("pl", "guests", "chat", True),
            ],
        )
        self.assertEqual(ParserSample.objects.get(stage="guests").parsed_result, "2")
        self.assertEqual(ParserSample.objects.get(text="tomorrow").parsed_result,
                         (timezone.localdate() + timedelta(days=1)).isoformat())

    def test_reviewed_result_resolves_later_voice_error_but_not_chat(self):
        en = self.date_steps()
        retry = self.answer(en, "two guess", voice=True, transcript=True)
        self.assertEqual(retry["stage"], "guests")
        sample = ParserSample.objects.get(text="two guess")
        sample.corrected_result = "2"
        sample.save(update_fields=["corrected_result"])
        retry = self.answer(retry, "two guess")
        self.assertEqual(retry["stage"], "guests")
        offers = self.answer(retry, "two guess", voice=True, transcript=True)
        self.assertEqual(offers["stage"], "room")
        confirmation = self.answer(offers, "option 1")
        self.assertEqual(confirmation["stage"], "confirm")
        self.assertEqual(ParserSample.objects.get(stage="room").parsed_result, "ROOM-2")
        self.assertFalse(Booking.objects.exists())

    def test_records_contact_parses_and_expires_contact_corrections(self):
        turn = self.date_steps()
        for text in ("2", "1", "yes", "email"):
            turn = self.answer(turn, text)
        turn = self.answer(turn, "guest monkey example dot com", voice=True, transcript=True)
        sample = ParserSample.objects.get(stage="email")
        self.assertFalse(sample.succeeded)
        sample.corrected_result = "guest@example.com"
        sample.save(update_fields=["corrected_result"])
        candidate = self.answer(turn, "guest monkey example dot com", voice=True, transcript=True)
        self.assertEqual(candidate["stage"], "contact_confirm")
        self.assertFalse(Booking.objects.exists())
        receipt = self.answer(candidate, "yes")
        self.assertEqual(receipt["stage"], "completed")
        self.assertEqual(Booking.objects.get().client.email, "guest@example.com")
        ParserSample.objects.filter(pk=sample.pk).update(created_at=timezone.now() - timedelta(days=31))
        call_command("expire_parser_samples", verbosity=0)
        self.assertFalse(ParserSample.objects.filter(pk=sample.pk).exists())

    @override_settings(HOTEL_PARSER_TRAINING_ENABLED=False)
    def test_disabled_tracking_uses_normal_parser(self):
        self.assertEqual(self.answer(self.start(), "tomorrow")["stage"], "check_out")
        self.assertFalse(ParserSample.objects.exists())

    def test_contact_details_entered_on_arrival_are_not_collected(self):
        turn = self.answer(self.start(), "Write me at guest@example.com")
        self.assertEqual(turn["stage"], "handover")
        self.assertFalse(ParserSample.objects.exists())

    def publish_parser_policy(self, scenario, actor, *, number, skip_stage=None):
        version = scenario.versions.create(number=number)
        steps = {
            key: version.steps.create(
                key=key, configuration={"parser_corrections": "skip"} if key == skip_stage else {},
            )
            for key in sorted(APPROVED_STEPS)
            if key != "welcome" or scenario.language == "pl"
        }
        ScenarioTransition.objects.bulk_create(
            ScenarioTransition(source=steps[source], event=event, target=steps[target])
            for source, outcomes in APPROVED_TRANSITIONS.items() if source in steps
            for event, target in outcomes.items()
        )
        review = review_scenario_selection(scenario.pk, version.pk, actor=actor, action="publish")
        publish_scenario_version(
            scenario.pk, version.pk, actor=actor, expected_version_id=review.expected_version_id,
            expected_audit_id=review.expected_audit_id, expected_fingerprint=review.fingerprint,
        )
        return version.pk

    def assert_pinned_correction_policy(
        self, *, stage, raw, corrected, direct, expected, channel, language, lead_up, state_field,
    ):
        actor = get_user_model().objects.create_user(username="scenario-publisher", is_staff=True, is_superuser=True)
        scenario = Scenario.objects.create(language=language, channel=channel)
        default_version = self.publish_parser_policy(scenario, actor, number=1)
        start_flags = {"voice": True} if channel == "voice" else {}
        answer_flags = {"voice": True, "transcript": True} if channel == "voice" else {}
        expected_stage = "confirm" if stage == "room" else "contact_confirm"

        turn = self.start(language, **start_flags)
        for text in lead_up:
            turn = self.answer(turn, text, **answer_flags)
        self.assertEqual(turn["stage"], stage)
        failed = self.answer(turn, raw, **answer_flags)
        self.assertEqual(failed["stage"], stage)
        sample = ParserSample.objects.get(stage=stage, text=raw, source=channel)
        self.assertFalse(sample.succeeded)
        sample.corrected_result = validate_correction(stage, raw, corrected, language=language, source=channel)
        sample.save(update_fields=["corrected_result"])

        skip_version = self.publish_parser_policy(scenario, actor, number=2, skip_stage=stage)
        applied = self.answer(failed, raw, **answer_flags)
        self.assertEqual(applied["stage"], expected_stage)
        state = Conversation.objects.get(session_id=self.client.session.session_key).state
        self.assertEqual(state["scenario_pin"]["version_id"], default_version)
        self.assertEqual(state[state_field], expected)

        turn = self.start(language, **start_flags)
        for text in lead_up:
            turn = self.answer(turn, text, **answer_flags)
        self.assertEqual(turn["stage"], stage)
        skipped = self.answer(turn, raw, **answer_flags)
        self.assertEqual(skipped["stage"], stage)
        state = Conversation.objects.get(session_id=self.client.session.session_key).state
        self.assertEqual(state["scenario_pin"]["version_id"], skip_version)
        self.assertNotIn(state_field, state)
        self.assertEqual(
            ParserSample.objects.filter(stage=stage, text=raw, source=channel, succeeded=False).count(), 3,
        )

        accepted = self.answer(skipped, direct, **answer_flags)
        self.assertEqual(accepted["stage"], expected_stage)
        state = Conversation.objects.get(session_id=self.client.session.session_key).state
        self.assertEqual(state[state_field], expected)
        self.assertFalse(Booking.objects.exists())

    def test_pinned_chat_room_policy_preserves_default_and_skips_when_configured(self):
        self.assert_pinned_correction_policy(
            stage="room", raw="double rum", corrected="option 1", direct="option 1",
            expected="ROOM-2", channel="chat", language="en", state_field="room",
            lead_up=("tomorrow", "for a week", "2"),
        )

    def test_pinned_voice_email_policy_preserves_default_and_skips_when_configured(self):
        self.assert_pinned_correction_policy(
            stage="email", raw="guest monkey example dot test", corrected="guest@example.test",
            direct="guest at example dot test", expected="guest@example.test",
            channel="voice", language="en", state_field="contact_candidate",
            lead_up=("tomorrow", "for a week", "2", "1", "yes", "email"),
        )

    def test_pinned_polish_voice_phone_policy_preserves_default_and_skips_when_configured(self):
        self.assert_pinned_correction_policy(
            stage="phone", raw="znak plusa 1 202 555 0142", corrected="+12025550142",
            direct="plus 1 202 555 0142", expected="+12025550142",
            channel="voice", language="pl", state_field="contact_candidate",
            lead_up=("jutro", "na tydzień", "2", "1", "tak", "sms"),
        )
