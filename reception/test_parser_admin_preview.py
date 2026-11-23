from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from bookings.models import Booking

from .models import ParserConfiguration, ParserSample


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
