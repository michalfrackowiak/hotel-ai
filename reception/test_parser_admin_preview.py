from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from bookings.models import Booking

from .models import ParserConfiguration, ParserSample
from .parsing import parse_stay_input


class ParserDatePreviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = get_user_model().objects.create_user(username="parser-viewer", is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(codename="view_parserconfiguration"))
        cls.staff = get_user_model().objects.create_user(username="unassigned-staff", is_staff=True)
        cls.en = ParserConfiguration.objects.get(language="en")
        cls.pl = ParserConfiguration.objects.get(language="pl")

    @staticmethod
    def url(configuration):
        return reverse("admin:reception_parserconfiguration_preview_dates", args=[configuration.pk])

    def test_preview_respects_staff_and_model_permissions(self):
        url = self.url(self.en)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"reply": "tomorrow"}).status_code, 403)
        replay = {"action": "replay", "example": "voice-arrival", "correction": "in two days"}
        self.assertEqual(self.client.post(url, replay).status_code, 403)
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(reverse("admin:reception_parserconfiguration_changelist")), url)
        self.assertContains(self.client.get(url), "from tomorrow for three days")
        self.assertEqual(self.client.get(reverse(
            "admin:reception_parserconfiguration_preview_dates", args=[99999],
        )).status_code, 404)
        self.assertEqual(self.client.put(url).status_code, 405)

    def test_preview_submission_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.viewer)
        url = self.url(self.en)
        client.get(url)
        data = {"stage": "check_in", "reply": "tomorrow"}
        self.assertEqual(client.post(url, data).status_code, 403)
        data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        self.assertEqual(client.post(url, data).status_code, 200)

    @patch("django.utils.timezone.localdate", return_value=date(2027, 4, 1))
    def test_bilingual_parser_stays_and_known_arrival(self, _date):
        self.client.force_login(self.viewer)
        for configuration, reply in ((self.en, "from tomorrow for three days"), (self.pl, "od jutra na trzy dni")):
            with self.subTest(language=configuration.language):
                response = self.client.post(self.url(configuration), {"stage": "check_in", "reply": reply})
                self.assertEqual(response.context["result"], {"arrival": date(2027, 4, 2), "departure": date(2027, 4, 5)})
                self.assertContains(response, "2027-04-05")
        response = self.client.post(self.url(self.en), {
            "stage": "check_out", "reply": "for three days", "check_in": "2027-04-11",
        })
        self.assertEqual(response.context["result"], {"departure": date(2027, 4, 14)})

    @patch("django.utils.timezone.localdate", return_value=date(2027, 4, 1))
    def test_uses_saved_format_settings_and_does_not_save_samples_or_configuration(self, _date):
        self.client.force_login(self.viewer)
        data = {"stage": "check_in", "reply": "04/05/2027"}
        self.assertEqual(self.client.post(self.url(self.en), data).context["result"], {"arrival": date(2027, 4, 5)})
        self.assertEqual(self.client.post(self.url(self.pl), data).context["result"], {"arrival": date(2027, 5, 4)})
        self.en.allow_slash_dates = False
        self.en.save(update_fields=["allow_slash_dates"])
        response = self.client.post(self.url(self.en), {**data, "allow_slash_dates": "on"})
        self.assertContains(response, "No date was extracted")
        self.en.refresh_from_db()
        self.assertFalse(self.en.allow_slash_dates)
        self.assertFalse(ParserSample.objects.exists())
        self.assertFalse(Booking.objects.exists())

    def test_displayed_reference_date_matches_relative_extraction(self):
        self.client.force_login(self.viewer)
        with patch("django.utils.timezone.localdate", side_effect=[date(2027, 4, 1), date(2027, 4, 2)]):
            response = self.client.post(self.url(self.en), {"stage": "check_in", "reply": "tomorrow"})
        self.assertEqual(response.context["today"], date(2027, 4, 1))
        self.assertEqual(response.context["result"], {"arrival": date(2027, 4, 2)})

    def test_invalid_example_fields_are_reported_without_a_result(self):
        self.client.force_login(self.viewer)
        response = self.client.post(self.url(self.en), {"stage": "check_out", "reply": "three days"})
        self.assertFormError(response.context["form"], "check_in", "Enter the known arrival date for this example.")
        response = self.client.post(self.url(self.en), {"stage": "fake", "reply": "x" * 501})
        self.assertEqual(set(response.context["form"].errors), {"stage", "reply"})
        self.assertIsNone(response.context["result"])

    @patch("django.utils.timezone.localdate", return_value=date(2027, 4, 1))
    def test_fictional_replay_compares_dates_without_reading_samples(self, _date):
        self.client.force_login(self.viewer)
        for configuration, correction in ((self.en, "in two days"), (self.pl, "za dwa dni")):
            with self.subTest(language=configuration.language), CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url(configuration), {
                    "action": "replay", "example": "voice-arrival", "correction": correction,
                })
                self.assertContains(response, "Matches with proposed correction")
                self.assertEqual(response.context["replay_summary"], {
                    "total": 5, "baseline_matches": 3, "effective_matches": 4, "improved": 1, "regressed": 0,
                })
                rows = {row["case"].key: row for row in response.context["replay"]}
                voice = rows["voice-arrival"]
                self.assertEqual(voice["expected"], date(2027, 4, 3))
                self.assertIsNone(voice["baseline"])
                self.assertEqual(voice["effective"], voice["expected"])
                self.assertFalse(voice["baseline_matches"])
                self.assertTrue(voice["effective_matches"])
                self.assertIsNone(rows["chat-arrival"]["effective"])
                self.assertEqual(rows["checkout"]["effective"], date(2027, 4, 5))
            self.assertFalse(any("reception_parsersample" in query["sql"] for query in queries))
        self.assertFalse(ParserSample.objects.exists())
        self.assertFalse(Booking.objects.exists())

    def test_fictional_replay_rejects_unknown_and_control_examples(self):
        self.client.force_login(self.viewer)
        url = self.url(self.en)
        response = self.client.post(url, {"action": "replay", "example": "unknown", "correction": "in two days"})
        self.assertIn("example", response.context["replay_form"].errors)
        self.assertIsNone(response.context["replay"])
        response = self.client.post(url, {
            "action": "replay", "example": "direct-arrival", "correction": "in two days",
        })
        self.assertIn("example", response.context["replay_form"].errors)
        self.assertIsNone(response.context["replay"])

    @patch("django.utils.timezone.localdate", return_value=date(2027, 4, 1))
    def test_fictional_departure_replay_uses_known_arrival(self, _date):
        self.client.force_login(self.viewer)
        for configuration, correction in ((self.en, "for three nights"), (self.pl, "na trzy noce")):
            with self.subTest(language=configuration.language):
                response = self.client.post(self.url(configuration), {
                    "action": "replay", "example": "voice-departure", "correction": correction,
                })
                rows = {row["case"].key: row for row in response.context["replay"]}
                self.assertEqual(rows["voice-departure"]["arrival"], date(2027, 4, 2))
                self.assertIsNone(rows["voice-departure"]["baseline"])
                self.assertEqual(rows["voice-departure"]["effective"], date(2027, 4, 5))
                self.assertEqual(rows["checkout"]["effective"], date(2027, 4, 5))

    @patch("django.utils.timezone.localdate", return_value=date(2027, 4, 1))
    def test_fictional_replay_counts_only_changes_in_expected_matches(self, _date):
        self.client.force_login(self.viewer)
        payload = {"action": "replay", "example": "voice-arrival", "correction": "in three days"}
        response = self.client.post(self.url(self.en), payload)
        self.assertEqual(response.context["replay_summary"], {
            "total": 5, "baseline_matches": 3, "effective_matches": 3, "improved": 0, "regressed": 0,
        })

        def improved_builtin(stage, reply, language, **kwargs):
            if reply == "we can arrive the day after to marrow":
                return date(2027, 4, 3)
            return parse_stay_input(stage, reply, language, **kwargs)

        # The built-in parser reads the same reply in both sources, but the proposal only changes voice.
        with patch("reception.parser_admin_preview.parse_stay_input", side_effect=improved_builtin):
            response = self.client.post(self.url(self.en), payload)
        self.assertEqual(response.context["replay_summary"], {
            "total": 5, "baseline_matches": 3, "effective_matches": 2, "improved": 0, "regressed": 1,
        })
