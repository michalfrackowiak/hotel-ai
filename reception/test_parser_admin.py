from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ParserSample


def create_sample(index, **attributes):
    return ParserSample.objects.create(**{
        "language": "en", "stage": "guests", "source": "voice", "text": "tree people",
        "parsed_result": "", "succeeded": False, "rule_key": f"{index:064x}",
        **attributes,
    })


class ParserSampleAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sample = create_sample(1)
        cls.staff = get_user_model().objects.create_user(username="parser-reviewer", is_staff=True)

    def setUp(self):
        self.list = reverse("admin:reception_parsersample_changelist")
        self.change = reverse("admin:reception_parsersample_change", args=(self.sample.pk,))
        self.delete = reverse("admin:reception_parsersample_delete", args=(self.sample.pk,))

    def grant(self, *actions):
        permissions = Permission.objects.filter(
            content_type__app_label="reception", codename__in=[f"{action}_parsersample" for action in actions],
        )
        self.staff.user_permissions.add(*permissions)

    def test_requires_admin_and_model_permissions(self):
        self.assertEqual(self.client.get(self.list).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.list).status_code, 403)
        self.grant("view")
        self.assertContains(self.client.get(self.change), "tree people")
        self.assertEqual(self.client.post(self.change, {"corrected_result": "three"}).status_code, 403)
        self.assertEqual(self.client.post(self.delete, {"post": "yes"}).status_code, 403)
        self.grant("add")
        self.assertEqual(self.client.get(reverse("admin:reception_parsersample_add")).status_code, 403)

    def test_reviews_and_clears_corrections_without_replacing_captured_input(self):
        self.grant("view", "change")
        self.client.force_login(self.staff)
        invalid = self.client.post(self.change, {"corrected_result": "unparseable result"})
        self.assertContains(invalid, "errorlist")
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.corrected_result, "")

        change = self.client.post(self.change, {"corrected_result": "3", "text": "different reply", "succeeded": True})
        self.assertRedirects(change, self.list)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.corrected_result, "3")
        self.assertEqual(self.sample.text, "tree people")
        self.assertFalse(self.sample.succeeded)
        self.assertRedirects(self.client.post(self.change, {"corrected_result": ""}), self.list)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.corrected_result, "")

    def test_can_disable_and_reenable_a_saved_correction(self):
        self.grant("view", "change")
        self.client.force_login(self.staff)
        self.assertRedirects(
            self.client.post(self.change, {"corrected_result": "3"}), self.list,
        )
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.corrected_result, "3")
        self.assertFalse(self.sample.correction_enabled)
        disabled = self.client.get(self.list, {"review": "disabled"})
        self.assertEqual([row.pk for row in disabled.context["cl"].result_list], [self.sample.pk])
        self.assertRedirects(
            self.client.post(self.change, {"corrected_result": "3", "correction_enabled": "on"}), self.list,
        )
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.corrected_result, "3")
        self.assertTrue(self.sample.correction_enabled)
        enabled = self.client.get(self.list, {"review": "enabled"})
        self.assertEqual([row.pk for row in enabled.context["cl"].result_list], [self.sample.pk])

    def test_correction_conflicts_follow_the_runtime_scope(self):
        self.grant("view")
        self.client.force_login(self.staff)
        self.sample.corrected_result = "3"
        self.sample.save(update_fields=["corrected_result"])
        scope = dict(rule_key=self.sample.rule_key, corrected_result="4")
        create_sample(2, **{**scope, "corrected_result": "3"})
        create_sample(3, source="chat", **scope)
        create_sample(4, language="pl", **scope)
        create_sample(5, stage="room", **scope)
        create_sample(6, corrected_result="4")
        create_sample(7, correction_enabled=False, **scope)
        expired = create_sample(8, **scope)
        ParserSample.objects.filter(pk=expired.pk).update(created_at=timezone.now() - timedelta(days=181))

        self.assertContains(self.client.get(self.change), "Eligible")
        competing = create_sample(9, **scope)
        filtered = self.client.get(self.list, {"review": "enabled"})
        self.assertContains(filtered, '<td class="field-correction_status">Conflicting</td>', count=3, html=True)
        self.assertContains(self.client.get(self.change), "Conflicting")
        ParserSample.objects.filter(pk=competing.pk).update(correction_enabled=False)
        self.assertContains(self.client.get(self.change), "Eligible")

    def test_can_disable_an_existing_correction_that_no_longer_validates(self):
        self.grant("view", "change")
        self.client.force_login(self.staff)
        ParserSample.objects.filter(pk=self.sample.pk).update(corrected_result="obsolete")
        self.assertRedirects(
            self.client.post(self.change, {"corrected_result": "obsolete"}), self.list,
        )
        self.sample.refresh_from_db()
        self.assertFalse(self.sample.correction_enabled)
        self.assertContains(
            self.client.post(self.change, {"corrected_result": "obsolete", "correction_enabled": "on"}),
            "errorlist",
        )
        self.sample.refresh_from_db()
        self.assertFalse(self.sample.correction_enabled)

    def test_filters_by_stage_source_outcome_and_review_status(self):
        other = create_sample(
            2, language="pl", stage="room", source="chat", text="1", succeeded=True,
            parsed_result="RM-2", corrected_result="RM-2",
        )
        self.grant("view")
        self.client.force_login(self.staff)
        result = self.client.get(self.list, {
            "language__exact": "pl", "stage__exact": "room", "source__exact": "chat", "succeeded__exact": "1",
            "review": "corrected",
        })
        self.assertEqual([row.pk for row in result.context["cl"].result_list], [other.pk])
        pending = self.client.get(self.list, {"review": "pending"})
        self.assertEqual([row.pk for row in pending.context["cl"].result_list], [self.sample.pk])

    def test_staff_can_delete_samples_with_delete_permission(self):
        self.grant("view", "delete")
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.post(self.delete, {"post": "yes"}), reverse("admin:index"))
        self.assertFalse(ParserSample.objects.filter(pk=self.sample.pk).exists())

    def test_expired_samples_are_hidden(self):
        now = timezone.now()
        contact = create_sample(2, stage="email", text="guest@example.test", corrected_result="guest@example.test")
        corrected = create_sample(3, corrected_result="3", correction_enabled=False)
        old_correction = create_sample(4, corrected_result="3")
        ParserSample.objects.update(created_at=now - timedelta(days=31))
        ParserSample.objects.filter(pk=old_correction.pk).update(created_at=now - timedelta(days=181))
        self.grant("view")
        self.client.force_login(self.staff)
        self.assertEqual(
            [row.pk for row in self.client.get(self.list).context["cl"].result_list], [corrected.pk],
        )
        expired = reverse("admin:reception_parsersample_change", args=(contact.pk,))
        self.assertEqual(self.client.get(expired).status_code, 302)
