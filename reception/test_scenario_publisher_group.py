from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Scenario, ScenarioPrompt, ScenarioTransition
from .scenario_validation import APPROVED_STEPS, APPROVED_TRANSITIONS


class ScenarioPublisherGroupTests(TestCase):
    def login_publisher(self):
        call_command("setup_scenario_publisher_group", stdout=StringIO())
        publisher = get_user_model().objects.create_user(username="publisher-fixture", is_staff=True)
        publisher.groups.add(Group.objects.get(name="Scenario publisher"))
        self.client.force_login(publisher)
        return publisher

    def test_publisher_reviews_publishes_and_rolls_back_with_audit(self):
        publisher = self.login_publisher()
        scenario = Scenario.objects.create(language="pl", channel="voice")
        versions = []
        for number in (1, 2):
            version = scenario.versions.create(number=number)
            steps = {key: version.steps.create(key=key) for key in sorted(APPROVED_STEPS)}
            ScenarioTransition.objects.bulk_create(
                ScenarioTransition(source=steps[source], event=event, target=steps[target])
                for source, outcomes in APPROVED_TRANSITIONS.items()
                for event, target in outcomes.items()
            )
            versions.append(version)

        first, second = versions
        for version, action in ((first, "publish"), (second, "publish"), (first, "rollback")):
            url = reverse("admin:reception_scenarioversion_selection", args=[version.pk, action])
            self.assertEqual(self.client.post(url).status_code, 409)
            review = self.client.get(url)
            self.assertEqual(review.status_code, 200)
            self.assertTrue(review.context["review_token"], review.context["error_messages"])
            self.assertRedirects(self.client.post(url, {"review_token": review.context["review_token"]}),
                                 reverse("admin:reception_scenarioversion_change", args=[version.pk]))
            scenario.refresh_from_db()
            self.assertEqual(scenario.published_version_id, version.pk)
            audit = scenario.publication_history.first()
            self.assertEqual((audit.action, audit.actor_id, audit.selected_version_id), (action, publisher.pk, version.pk))

        history = self.client.get(reverse("admin:reception_scenarioversion_selection", args=[first.pk, "publish"]))
        self.assertEqual(len(history.context["history"]), 3)

    def test_publisher_can_inspect_but_cannot_edit_drafts_or_live_prompts(self):
        self.login_publisher()
        scenario = Scenario.objects.create(language="en", channel="chat")
        version = scenario.versions.create(number=1)
        step = version.steps.create(key="check_in")
        transition = step.outgoing_transitions.create(event="continue", target=version.steps.create(key="check_out"))
        self.assertContains(self.client.get(reverse("admin:reception_scenarioversion_flow", args=[version.pk])),
                            "Scenario flow overview")
        prompt = ScenarioPrompt.objects.get(language="en", key="check_in")
        for route, args in (
            ("scenario_change", [scenario.pk]), ("scenarioversion_change", [version.pk]),
            ("scenarioversion_add", []), ("scenariostep_change", [step.pk]), ("scenariostep_delete", [step.pk]),
            ("scenariotransition_change", [transition.pk]), ("scenarioprompt_change", [prompt.pk]),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(reverse(f"admin:reception_{route}", args=args)).status_code, 403)
