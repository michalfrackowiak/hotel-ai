import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import SpeechDeployment, SpeechModel, SpeechProvider, SpeechSelection
from .preflight import check_speech_plan


IMAGE = "example.test/speech@sha256:" + "a" * 64
POLICY = {"host": {"architecture": "arm64", "cpu_limit": 2, "memory_mib": 2048,
                   "approved_images": [IMAGE]}}


@override_settings(HOTEL_SPEECH_HOST_POLICIES=json.dumps(POLICY))
class PreflightTests(TestCase):
    def setUp(self):
        for task in ("stt", "tts"):
            provider = SpeechProvider.objects.create(name=task, slug=task, task=task, engine="fixture",
                languages=["en", "pl"], architecture="arm64", image=IMAGE, license="Fixture license")
            for language in ("en", "pl"):
                SpeechModel.objects.create(provider=provider, language=language, name=language,
                    checksum="b" * 64, license="Fixture license")
            deployment = SpeechDeployment.objects.create(name=task, slug=task, provider=provider,
                host="host", container_name=task)
            SpeechSelection.objects.filter(task=task).update(deployment=deployment)

    def test_shared_budget_boundary_and_overflow(self):
        result = check_speech_plan()
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual((result["totals"][0]["deployments"], result["totals"][0]["cpu"],
                          result["totals"][0]["memory_mib"]), (2, Decimal(2), 2048))
        for change in ({"cpu_limit": 1.99}, {"memory_mib": 2047}):
            with self.subTest(change=change), override_settings(
                    HOTEL_SPEECH_HOST_POLICIES=json.dumps({"host": POLICY["host"] | change})):
                self.assertFalse(check_speech_plan()["passed"])
        for language, task in (("pl", "tts"), ("pl", "stt")):
            source = SpeechDeployment.objects.get(name=task)
            deployment = SpeechDeployment.objects.create(name=language + task, slug=language + task,
                provider=source.provider, host="host", container_name=language + task)
            SpeechSelection.objects.filter(language=language, task=task).update(deployment=deployment)
            result = check_speech_plan()
            self.assertFalse(result["passed"])
            self.assertEqual(result["totals"][0]["deployments"], 3 if task == "tts" else 4)
            with override_settings(HOTEL_SPEECH_HOST_POLICIES=json.dumps({"host": POLICY["host"] | {
                    "cpu_limit": 4, "memory_mib": 4096}})):
                self.assertTrue(check_speech_plan()["passed"])

    def test_unassigned_mismatch_and_metadata(self):
        slot = SpeechSelection.objects.get(language="pl", task="stt")
        slot.deployment = None
        slot.save()
        self.assertFalse(check_speech_plan()["passed"])
        slot.deployment = SpeechDeployment.objects.get(name="tts")
        slot.save()
        self.assertTrue(any("required task" in issue for issue in check_speech_plan()["issues"]))
        slot.deployment = SpeechDeployment.objects.get(name="stt")
        slot.save()
        provider = slot.deployment.provider
        for change in ({"image": ""}, {"image": "example.test/other@sha256:" + "a" * 64},
                       {"architecture": "amd64"}, {"license": ""}, {"languages": []}, {"languages": 42}):
            with self.subTest(change=change):
                SpeechProvider.objects.filter(pk=provider.pk).update(**change)
                self.assertFalse(check_speech_plan()["passed"])
                provider.save()
        SpeechModel.objects.filter(provider=provider, language="pl").update(checksum="")
        self.assertTrue(any("checksum and license" in issue for issue in check_speech_plan()["issues"]))

    def test_invalid_policy_fails_closed_without_echoing_configuration(self):
        invalid = ["secret-invalid-json", "[]", "null", '{"host":{},"host":{}}', "1e9999999999999999999"]
        for change in ({"cpu_limit": True}, {"cpu_limit": "2"}, {"cpu_limit": float("nan")},
                       {"cpu_limit": float("inf")}, {"cpu_limit": 0}, {"memory_mib": True},
                       {"memory_mib": 2.5}, {"architecture": "unknown"},
                       {"approved_images": ["secret-unpinned:latest"]}, {"extra": "secret"}):
            invalid.append(json.dumps({"host": POLICY["host"] | change}))
        for raw in invalid:
            with self.subTest(raw=raw), override_settings(HOTEL_SPEECH_HOST_POLICIES=raw):
                result = check_speech_plan()
                self.assertFalse(result["passed"])
                self.assertIn("Invalid HOTEL_SPEECH_HOST_POLICIES", result["issues"][0])
                self.assertNotIn("secret", str(result))
        with override_settings(HOTEL_SPEECH_HOST_POLICIES="{}"):
            self.assertTrue(any("no locally approved policy" in issue for issue in check_speech_plan()["issues"]))

    def test_private_get_only_page_with_view_permissions_and_no_writes(self):
        url = reverse("admin:model_providers_speechselection_preflight")
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user("viewer")
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 302)
        user.is_staff = True
        user.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        models = (SpeechProvider, SpeechModel, SpeechDeployment, SpeechSelection)
        snapshot = [list(model.objects.values()) for model in models]
        for model in models:
            user.user_permissions.add(Permission.objects.get(codename=f"view_{model._meta.model_name}"))
        response = self.client.get(url)
        self.assertContains(response, "Static plan checks passed")
        self.assertContains(response, "not authorization to activate")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(snapshot, [list(model.objects.values()) for model in models])
        response = self.client.get(reverse("admin:model_providers_speechselection_changelist"))
        self.assertContains(response, "Check complete speech plan")
        user.user_permissions.remove(Permission.objects.get(codename="view_speechmodel"))
        self.assertEqual(self.client.get(url).status_code, 403)
