from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from .models import SpeechDeployment, SpeechProvider, SpeechSelection


class SpeechPlanTests(TestCase):
    def deployment(self, name, task="stt", languages=None):
        provider = SpeechProvider.objects.create(
            name=name, slug=name, task=task, engine="fixture", languages=languages or ["en", "pl"],
            architecture="arm64",
        )
        return SpeechDeployment.objects.create(name=name, slug=name, provider=provider,
                                               host="fixture-host", container_name=name)

    def assign(self, language, task, deployment):
        slot = SpeechSelection.objects.get(language=language, task=task)
        slot.deployment = deployment
        slot.full_clean()
        slot.save()
        return slot

    def test_four_empty_slots_and_two_three_four_deployment_shapes(self):
        self.assertEqual(SpeechSelection.objects.count(), 4)
        self.assertFalse(SpeechSelection.objects.filter(deployment__isnull=False).exists())
        stt, tts = self.deployment("stt"), self.deployment("tts", "tts")
        for language in ("en", "pl"):
            self.assign(language, "stt", stt)
            self.assign(language, "tts", tts)
        deployment_admin = admin.site._registry[SpeechDeployment]
        self.assertEqual(deployment_admin.reference_count(stt), 2)
        self.assertEqual(deployment_admin.reference_count(tts), 2)
        self.assign("pl", "stt", self.deployment("polish-stt", languages=["pl"]))
        self.assertEqual(SpeechSelection.objects.values("deployment").distinct().count(), 3)
        self.assertEqual(deployment_admin.reference_count(stt), 1)
        self.assign("pl", "tts", self.deployment("polish-tts", "tts", ["pl"]))
        self.assertEqual(SpeechSelection.objects.values("deployment").distinct().count(), 4)

    def test_validation_protection_and_parent_mutation(self):
        deployment = self.deployment("english", languages=["en"])
        for language, task in (("pl", "stt"), ("en", "tts")):
            with self.subTest(language=language, task=task), self.assertRaises(ValidationError):
                self.assign(language, task, deployment)
        self.assign("en", "stt", deployment)
        with self.assertRaises(ProtectedError):
            deployment.delete()
        with self.assertRaises(ProtectedError):
            deployment.provider.delete()
        for changes in ({"task": "tts"}, {"languages": ["en", "pl"]}):
            provider = SpeechProvider.objects.get(pk=deployment.provider_id)
            for key, value in changes.items():
                setattr(provider, key, value)
            with self.assertRaises(ValidationError):
                provider.full_clean()
        deployment.provider = self.deployment("other").provider
        with self.assertRaises(ValidationError):
            deployment.full_clean()
        SpeechSelection.objects.update(deployment=None)
        deployment.full_clean()
        deployment.save()

    def test_database_slots_and_identifier_validation(self):
        for values in ({"language": "en", "task": "stt"}, {"language": "de", "task": "stt"},
                       {"language": "en", "task": "bad"}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                SpeechSelection.objects.create(**values)
        deployment = self.deployment("valid")
        for field, value in (("host", "https://example.test"), ("container_name", "name; command"),
                             ("observed_state", "running")):
            item = SpeechDeployment.objects.get(pk=deployment.pk)
            setattr(item, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                item.full_clean()
        duplicate = SpeechDeployment(name="duplicate", slug="duplicate", provider=deployment.provider,
                                     host=deployment.host, container_name=deployment.container_name)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_admin_permissions_fixed_slots_and_mismatch(self):
        slot = SpeechSelection.objects.get(language="en", task="stt")
        url = reverse("admin:model_providers_speechselection_change", args=[slot.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = get_user_model().objects.create_user("planner", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="change_speechselection"))
        self.client.force_login(staff)
        wrong = self.deployment("wrong", "tts")
        self.assertEqual(self.client.post(url, {"deployment": wrong.pk}).status_code, 200)
        slot.refresh_from_db()
        self.assertIsNone(slot.deployment_id)
        right = self.deployment("right")
        self.assertEqual(self.client.post(url, {"deployment": right.pk, "language": "pl", "task": "tts"}).status_code, 302)
        slot.refresh_from_db()
        self.assertEqual((slot.language, slot.task, slot.deployment_id), ("en", "stt", right.pk))
        staff.is_superuser = True
        staff.save()
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("admin:model_providers_speechselection_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:model_providers_speechselection_delete", args=[slot.pk])).status_code, 403)

    def test_limited_staff_cannot_invalidate_selected_provider_or_deployment(self):
        deployment = self.deployment("selected")
        self.assign("en", "stt", deployment)
        other = self.deployment("other")
        staff = get_user_model().objects.create_user("editor", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=("change_speechprovider", "change_speechdeployment")))
        self.client.force_login(staff)
        provider = deployment.provider
        response = self.client.post(reverse("admin:model_providers_speechprovider_change", args=[provider.pk]), {
            "name": provider.name, "slug": provider.slug, "task": "tts", "engine": "fixture", "languages": ["en"],
            "architecture": "arm64", "cpu_limit": "1", "memory_mib": "1024",
        })
        self.assertContains(response, "Clear planned assignments")
        provider.refresh_from_db()
        self.assertEqual(provider.task, "stt")
        self.assertEqual(provider.languages, ["en", "pl"])
        response = self.client.post(reverse("admin:model_providers_speechdeployment_change", args=[deployment.pk]), {
            "name": deployment.name, "slug": deployment.slug, "provider": other.provider_id,
            "host": deployment.host, "container_name": deployment.container_name,
        })
        self.assertContains(response, "Clear planned assignments")
        deployment.refresh_from_db()
        self.assertEqual(deployment.provider_id, provider.pk)
