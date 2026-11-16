import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import SpeechDeployment, SpeechModel, SpeechProvider, SpeechSelection
from .transition_budget import transition_budget_report


IMAGE = "example.test/speech@sha256:" + "a" * 64
POLICY = {"host": {"architecture": "arm64", "cpu_limit": 2, "memory_mib": 2048,
                   "approved_images": [IMAGE]}}


def observed(name, **changes):
    return dict(container_name=name, configured_image=IMAGE, state="running",
                cpu_limit=1, memory_mib=1024) | changes


@override_settings(HOTEL_SPEECH_HOST_POLICIES=json.dumps(POLICY))
class TransitionBudgetTests(TestCase):
    def setUp(self):
        for task in ("stt", "tts"):
            provider = SpeechProvider.objects.create(name=task, slug=task, task=task, engine="fixture",
                languages=["en", "pl"], architecture="arm64", image=IMAGE, license="Fixture")
            for language in ("en", "pl"):
                SpeechModel.objects.create(provider=provider, language=language, name=language,
                    checksum="b" * 64, license="Fixture")
            deployment = SpeechDeployment.objects.create(name=task, slug=task, provider=provider,
                host="host", container_name=task)
            SpeechSelection.objects.filter(task=task).update(deployment=deployment)

    def report(self, records, host="host"):
        with patch("model_providers.transition_budget.fetch_inventory", return_value={
                "host": host, "observed_at": "fixture timestamp", "deployments": records}):
            return transition_budget_report()

    def test_shared_exact_boundary_and_unplanned_overlap(self):
        result = self.report([observed("stt"), observed("tts")])
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual((result["active_count"], result["planned_count"], result["overlap_cpu"]), (2, 2, 2))
        for state in ("running", "paused", "restarting", "removing"):
            with self.subTest(state=state):
                result = self.report([observed("stt"), observed("tts"), observed("old-shared", state=state)])
                self.assertFalse(result["passed"])
                self.assertEqual(result["overlap_cpu"], 3)
                self.assertEqual(result["overlap_memory"], 3072)
                self.assertTrue(any("CPU exceeds" in issue for issue in result["issues"]))

    def test_inactive_and_unobserved_plans_add_limits(self):
        for state in ("created", "exited", "dead"):
            with self.subTest(state=state):
                result = self.report([observed("stt", state=state), observed("unplanned", state=state)])
                self.assertTrue(result["passed"], result["issues"])
                self.assertEqual((result["active_cpu"], result["overlap_cpu"]), (0, 2))
                self.assertTrue(any("unobserved" in row["action"] for row in result["rows"]))

    def test_unknown_limits_name_collision_and_other_hosts_block(self):
        for change in ({"cpu_limit": None}, {"memory_mib": None}, {"cpu_limit": 0.5},
                       {"configured_image": "different"}):
            for state in ("running", "created"):
                with self.subTest(change=change, state=state):
                    result = self.report([observed("stt", state=state, **change)])
                    self.assertFalse(result["passed"])
                    self.assertIsNone(result["overlap_cpu"])
        result = self.report([observed("unplanned", cpu_limit=None)])
        self.assertFalse(result["passed"])
        self.assertTrue(any("unknown configured limits" in issue for issue in result["issues"]))
        result = self.report([observed("stt")], host="other-host")
        self.assertFalse(result["passed"])
        self.assertTrue(any("All planned hosts" in issue for issue in result["issues"]))

    def test_static_failure_skips_network_and_catalog_snapshot_precedes_fetch(self):
        SpeechProvider.objects.filter(task="stt").update(languages=42)
        with patch("model_providers.transition_budget.fetch_inventory") as fetch:
            result = transition_budget_report()
            self.assertFalse(result["passed"])
            fetch.assert_not_called()
        SpeechProvider.objects.filter(task="stt").update(languages=["en", "pl"])

        def change_after_snapshot():
            SpeechProvider.objects.filter(task="stt").update(image="changed")
            return {"host": "host", "observed_at": "fixture", "deployments": [observed("stt"), observed("tts")]}

        with patch("model_providers.transition_budget.fetch_inventory", side_effect=change_after_snapshot):
            self.assertTrue(transition_budget_report()["passed"])

    def test_admin_permissions_get_only_no_cache_and_no_writes(self):
        url = reverse("admin:model_providers_speechselection_transition")
        user = get_user_model().objects.create_user("budget-viewer", is_staff=True)
        with patch("model_providers.transition_budget.fetch_inventory", return_value={
                "host": "host", "observed_at": "fixture", "deployments": [observed("stt"), observed("tts")]}) as fetch:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            fetch.assert_not_called()
            for model in ("speechselection", "speechdeployment", "speechprovider", "speechmodel"):
                user.user_permissions.add(Permission.objects.get(codename="view_" + model))
            models = (SpeechSelection, SpeechDeployment, SpeechProvider, SpeechModel)
            before = [list(model.objects.values()) for model in models]
            response = self.client.get(url)
            self.assertContains(response, "Configured overlap fits reserved budget")
            self.assertContains(response, "No local resource admission policy")
            self.assertNotContains(response, "Resource preview checks passed")
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(self.client.post(url).status_code, 405)
            self.assertEqual(before, [list(model.objects.values()) for model in models])
            user.user_permissions.remove(Permission.objects.get(codename="view_speechmodel"))
            self.assertEqual(self.client.get(url).status_code, 403)
