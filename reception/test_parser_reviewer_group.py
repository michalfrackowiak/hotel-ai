from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from reception.models import ParserSample


class ParserReviewerGroupTests(TestCase):
    def setup_group(self):
        call_command("setup_parser_reviewer_group", stdout=StringIO())
        return Group.objects.get(name="Parser reviewer")

    def test_setup_is_idempotent_and_leaves_accounts_unchanged(self):
        user = get_user_model().objects.create_user(username="parser-fixture")
        group = self.setup_group()
        self.assertFalse(group.user_set.exists())
        group.user_set.add(user)
        self.assertEqual(group.pk, self.setup_group().pk)
        self.assertEqual(list(group.user_set.all()), [user])
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(set(group.permissions.values_list("codename", flat=True)), {
            "view_parsersample", "change_parsersample",
        })

    def test_setup_refuses_existing_group_with_extra_permissions(self):
        group = Group.objects.create(name="Parser reviewer")
        unexpected = Permission.objects.get(content_type__app_label="reception", codename="delete_parsersample")
        group.permissions.add(unexpected)
        with self.assertRaisesMessage(CommandError, "has unexpected permissions"):
            self.setup_group()
        self.assertEqual(list(group.permissions.all()), [unexpected])

    def test_role_can_review_samples_only(self):
        user = get_user_model().objects.create_user(username="reviewer-fixture", is_staff=True)
        user.groups.add(self.setup_group())
        self.client.force_login(user)
        sample = ParserSample.objects.create(
            language="en", stage="guests", source="voice", text="Fictional reply", succeeded=False,
        )
        changelist = reverse("admin:reception_parsersample_changelist")
        detail = reverse("admin:reception_parsersample_change", args=[sample.pk])
        self.assertContains(self.client.get(changelist), sample.text)
        self.assertContains(self.client.get(detail), sample.text)
        self.assertRedirects(self.client.post(detail, {
            "corrected_result": "2", "correction_enabled": "on",
        }), changelist)
        sample.refresh_from_db()
        self.assertEqual(sample.corrected_result, "2")
        self.assertEqual(sample.text, "Fictional reply")
        self.assertEqual(self.client.post(reverse("admin:reception_parsersample_delete", args=[sample.pk]), {
            "post": "yes",
        }).status_code, 403)
        self.assertTrue(ParserSample.objects.filter(pk=sample.pk).exists())

        for url_name in (
            "admin:reception_parsersample_add",
            "admin:reception_parserconfiguration_changelist",
            "admin:reception_staffhandover_changelist",
            "admin:reception_scenarioversion_changelist",
            "admin:bookings_booking_changelist",
            "admin:auth_user_changelist",
            "admin:model_providers_speechselection_changelist",
        ):
            with self.subTest(url_name=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)
