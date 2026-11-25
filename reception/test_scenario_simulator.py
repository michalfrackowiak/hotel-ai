from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bookings.models import Booking, BookingConfirmation

from .models import ParserSample, Scenario, ScenarioStep, ScenarioTransition, ScenarioVersion, StaffHandover
from .scenario_validation import APPROVED_STEPS, APPROVED_TRANSITIONS


class ScenarioSimulatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser(username="simulator-reviewer")

    def setUp(self):
        self.client.force_login(self.staff)

    def make_version(self, language, channel, configurations=None, extra_edges=()):
        scenario = Scenario.objects.create(language=language, channel=channel)
        version = ScenarioVersion.objects.create(scenario=scenario, number=1)
        configurations = configurations or {}
        steps = {
            key: ScenarioStep.objects.create(version=version, key=key, configuration=configurations.get(key, {}))
            for key in sorted(APPROVED_STEPS)
            if key != "welcome" or language == "pl"
        }
        edges = [
            (source, event, target)
            for source, outcomes in APPROVED_TRANSITIONS.items()
            if source in steps
            for event, target in outcomes.items()
        ]
        ScenarioTransition.objects.bulk_create(
            ScenarioTransition(source=steps[source], event=event, target=steps[target])
            for source, event, target in (*edges, *extra_edges)
        )
        return version

    def preview(self, version, stage, **values):
        response = self.client.post(
            reverse("admin:reception_scenarioversion_flow", args=[version.pk]),
            {"stage": stage, **values},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["flow_errors"], [])
        self.assertEqual(response.context["simulation_form"].errors, {})
        return response.context["simulation"]

    def test_polish_welcome_language_switch_uses_builtin_english_arrival(self):
        version = self.make_version("pl", "voice")

        preview = self.preview(version, "welcome", reply="English")

        self.assertEqual((preview["language"], preview["stage"]), ("en", "check_in"))
        self.assertIn("please state your planned arrival date", preview["chat"])
        self.assertTrue(preview["voice"])
        self.assertEqual({part["language"] for part in preview["voice"]}, {"en"})
        self.assertEqual(preview["decisions"], [])

    def test_final_voice_silence_can_keep_chat_open_without_changing_chat_timeout(self):
        version = self.make_version("pl", "voice", {"welcome": {"voice_timeout": "continue_in_chat"}})

        voice = self.preview(version, "welcome_voice_timeout")
        chat = self.preview(version, "welcome_timeout")

        self.assertTrue(voice["continue_in_chat"])
        self.assertEqual(voice["stage"], "welcome")
        self.assertTrue(voice["chat"])
        self.assertEqual((voice["voice"], voice["voice_fallback"], voice["decisions"]), ([], "", []))
        self.assertFalse(chat["continue_in_chat"])
        self.assertEqual(chat["stage"], "check_in")
        self.assertEqual(chat["decisions"], [("welcome", "timeout", "check_in")])

    def test_bilingual_arrival_preview_extracts_a_stay_from_noisy_input(self):
        today = date(2030, 1, 15)
        for language, reply in (
            ("en", "strike of will be the from tomorrow for three days"),
            ("pl", "trzask będzie od jutra na trzy dni"),
        ):
            with self.subTest(language=language):
                version = self.make_version(language, "chat", {"check_in": {"arrival_input": "require_range"}})
                with patch("reception.parsing.timezone.localdate", return_value=today):
                    preview = self.preview(version, "check_in", reply=reply)

                self.assertEqual(preview["arrival"], (today + timedelta(days=1)).isoformat())
                self.assertEqual(preview["departure"], (today + timedelta(days=4)).isoformat())
                self.assertEqual(preview["stage"], "guests")
                self.assertEqual(preview["decisions"], [("check_in", "stay_dates", "guests")])

    def test_guest_preview_applies_fictional_correction_and_model_only_when_allowed(self):
        version = self.make_version("en", "chat")
        corrected = self.preview(version, "guests", reply="garbled party", fictional_correction="2")
        fallback = self.preview(version, "guests", reply="garbled party", fictional_model_value="2")
        parsed = self.preview(version, "guests", reply="three guests", fictional_model_value="2")

        self.assertTrue(corrected["correction_applied"])
        self.assertIn("Used", fallback["model_outcome"])
        for preview in (corrected, fallback):
            self.assertEqual((preview["stage"], preview["guests"]), ("room", 2))
            self.assertIn("Demo Double", preview["chat"])
            self.assertIn("Demo Family", preview["chat"])
        self.assertEqual((parsed["stage"], parsed["guests"]), ("room", 3))
        self.assertIn("built-in parser found", parsed["model_outcome"])

        guest_step = version.steps.get(key="guests")
        guest_step.configuration = {"parser_corrections": "skip", "parser_fallback": "built_in_only"}
        guest_step.save(update_fields=["configuration"])
        skipped = self.preview(
            version, "guests", reply="garbled party", fictional_correction="2", fictional_model_value="2",
        )
        self.assertFalse(skipped["correction_applied"])
        self.assertIn("built-in parser only", skipped["model_outcome"])
        self.assertEqual((skipped["stage"], skipped["guests"]), ("guests", None))
        self.assertEqual(skipped["decisions"], [("guests", "retry", "guests")])
        self.assertFalse(ParserSample.objects.exists())

    def test_fictional_room_correction_respects_options_and_pinned_policy(self):
        version = self.make_version("en", "chat")
        with patch("reception.parser_training.Room.objects.filter", side_effect=AssertionError("Looked up real rooms")):
            corrected = self.preview(version, "room", reply="rum fumble", fictional_correction="Demo Family")
            unavailable = self.preview(version, "room", reply="rum fumble", fictional_correction="option 3")
            step = version.steps.get(key="room")
            step.configuration = {"parser_corrections": "skip"}
            step.save(update_fields=["configuration"])
            skipped = self.preview(version, "room", reply="rum fumble", fictional_correction="2")

        self.assertTrue(corrected["correction_applied"])
        self.assertEqual((corrected["stage"], corrected["selected_room"]), ("confirm", "Demo Family"))
        self.assertFalse(unavailable["correction_applied"])
        self.assertEqual(unavailable["stage"], "room")
        self.assertFalse(skipped["corrections_allowed"])
        self.assertEqual(skipped["stage"], "room")

    def test_fictional_contact_corrections_enter_review_without_saving(self):
        for language, stage, reply, correction in (
            ("en", "email", "guest monkey example dot com", "guest@example.com"),
            ("pl", "phone", "znak plusa 1 202 555 0142", "+12025550142"),
        ):
            with self.subTest(stage=stage):
                version = self.make_version(language, "voice")
                preview = self.preview(version, stage, reply=reply, fictional_correction=correction)
                self.assertTrue(preview["correction_applied"])
                self.assertEqual((preview["stage"], preview["contact_candidate"]), ("contact_confirm", correction))
                step = version.steps.get(key=stage)
                step.configuration = {"parser_corrections": "skip"}
                step.save(update_fields=["configuration"])
                skipped = self.preview(version, stage, reply=reply, fictional_correction=correction)
                self.assertFalse(skipped["corrections_allowed"])
                self.assertEqual((skipped["stage"], skipped["contact_candidate"]), (stage, None))
        self.assertFalse(ParserSample.objects.exists())
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(BookingConfirmation.objects.exists())

    def test_simulator_rejects_unsafe_fictional_corrections(self):
        version = self.make_version("en", "chat")
        for stage, reply, correction in (
            ("room", "Demo Double", "Demo Family"),
            ("room", "rum fumble", "other room"),
            ("email", "guest monkey example dot com", "other@example.com"),
            ("email", "guest monkey private dot example dot com", "guest@private.example.com"),
            ("phone", "plus sign 1 202 555 0142", "+12025550143"),
            ("phone", "plus sign 1 202 555 0200", "+12025550200"),
        ):
            with self.subTest(stage=stage, correction=correction):
                response = self.client.post(
                    reverse("admin:reception_scenarioversion_flow", args=[version.pk]),
                    {"stage": stage, "reply": reply, "fictional_correction": correction},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("fictional_correction", response.context["simulation_form"].errors)
                self.assertIsNone(response.context["simulation"])

    def test_retry_limit_previews_handover_without_creating_records(self):
        version = self.make_version(
            "en", "voice", {"guests": {"retry_limit": 2}}, (("guests", "retry_exhausted", "handover"),),
        )

        retry = self.preview(version, "guests", reply="garbled party")
        exhausted = self.preview(version, "guests", reply="garbled party", prior_retries="1")

        self.assertEqual((retry["stage"], retry["resulting_retry_count"]), ("guests", 1))
        self.assertFalse(retry["retry_handover"])
        self.assertEqual(exhausted["stage"], "handover")
        self.assertTrue(exhausted["retry_handover"])
        self.assertEqual(exhausted["decisions"], [
            ("guests", "retry", "guests"), ("guests", "retry_exhausted", "handover"),
        ])
        self.assertFalse(StaffHandover.objects.exists())
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(BookingConfirmation.objects.exists())
