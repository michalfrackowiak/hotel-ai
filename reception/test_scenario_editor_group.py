from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Scenario, ScenarioPrompt, ScenarioStep, ScenarioTransition, ScenarioVersion


class ScenarioEditorGroupTests(TestCase):
    def setup_group(self):
        call_command("setup_scenario_editor_group", stdout=StringIO())
        return Group.objects.get(name="Scenario editor")

    def login_editor(self):
        editor = get_user_model().objects.create_user(username="scenario-fixture", is_staff=True)
        editor.groups.add(self.setup_group())
        self.client.force_login(editor)

    def test_setup_is_repeatable_and_refuses_publication_permission_in_existing_group(self):
        group = self.setup_group()
        self.assertEqual(self.setup_group().pk, group.pk)
        self.assertFalse(group.user_set.exists())
        publication_grant = Permission.objects.get(
            content_type__app_label="reception", codename="publish_scenario",
        )
        self.assertFalse(group.permissions.filter(pk=publication_grant.pk).exists())
        group.permissions.add(publication_grant)
        with self.assertRaisesMessage(CommandError, "has unexpected permissions"):
            self.setup_group()

    def test_editor_can_author_and_inspect_draft_graph(self):
        self.login_editor()
        scenario = Scenario.objects.create(language="en", channel="chat")
        self.assertEqual(self.client.get(reverse("admin:reception_scenario_change", args=[scenario.pk])).status_code, 200)
        self.assertRedirects(self.client.post(reverse("admin:reception_scenarioversion_add"), {
            "scenario": scenario.pk, "number": 1,
        }), reverse("admin:reception_scenarioversion_changelist"))
        version = scenario.versions.get(number=1)
        step_list = reverse("admin:reception_scenariostep_changelist")
        self.assertRedirects(self.client.post(reverse("admin:reception_scenariostep_add"), {
            "version": version.pk, "key": "check_in", "position": 0,
        }), step_list)
        step = version.steps.get(key="check_in")
        self.assertRedirects(self.client.post(reverse("admin:reception_scenariostep_change", args=[step.pk]), {
            "position": 0, "chat_text": "Fictional arrival prompt.",
        }), step_list)
        step.refresh_from_db()
        self.assertEqual(step.configuration["chat_text"], "Fictional arrival prompt.")

        target = version.steps.create(key="check_out", position=1)
        transition_list = reverse("admin:reception_scenariotransition_changelist")
        self.assertRedirects(self.client.post(reverse("admin:reception_scenariotransition_add"), {
            "source": step.pk, "target": target.pk, "event": "continue",
        }), transition_list)
        transition = step.outgoing_transitions.get()
        overview = reverse("admin:reception_scenarioversion_flow", args=[version.pk])
        self.assertContains(self.client.get(overview), "Scenario flow overview")
        self.assertEqual(self.client.post(reverse("admin:reception_scenariostep_delete", args=[step.pk]), {
            "post": "yes",
        }).status_code, 403)
        self.assertRedirects(self.client.post(reverse("admin:reception_scenariotransition_delete", args=[transition.pk]), {
            "post": "yes",
        }), transition_list)
        self.assertFalse(ScenarioTransition.objects.filter(pk=transition.pk).exists())
        self.assertRedirects(self.client.post(reverse("admin:reception_scenariostep_delete", args=[step.pk]), {
            "post": "yes",
        }), step_list)
        self.assertFalse(ScenarioStep.objects.filter(pk=step.pk).exists())

    def test_editor_cannot_publish_or_change_live_content(self):
        self.login_editor()
        scenario = Scenario.objects.create(language="en", channel="chat")
        draft = scenario.versions.create(number=1)
        sealed = scenario.versions.create(number=2)
        live_step = sealed.steps.create(key="check_in", position=0)
        ScenarioVersion.objects.filter(pk=sealed.pk).update(published_at=timezone.now())
        Scenario.objects.filter(pk=scenario.pk).update(published_version=sealed)
        prompt = ScenarioPrompt.objects.get(language="en", key="check_in")

        for route, args in (
            ("reception_scenarioversion_selection", [draft.pk, "publish"]),
            ("reception_scenarioversion_selection", [sealed.pk, "rollback"]),
            ("reception_scenarioversion_change", [sealed.pk]),
            ("reception_scenarioversion_delete", [draft.pk]),
            ("reception_scenariostep_change", [live_step.pk]),
            ("reception_scenariostep_delete", [live_step.pk]),
            ("reception_scenarioprompt_change", [prompt.pk]),
            ("reception_scenario_change", [scenario.pk]),
        ):
            with self.subTest(route=route, args=args):
                self.assertEqual(self.client.post(reverse(f"admin:{route}", args=args)).status_code, 403)

        response = self.client.post(reverse("admin:reception_scenariostep_add"), {
            "version": sealed.pk, "key": "check_out", "position": 1,
        })
        self.assertContains(response, "Select a valid choice")
        self.assertEqual(sealed.steps.count(), 1)
