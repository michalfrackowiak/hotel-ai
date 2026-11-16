from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import SpeechModel, SpeechProvider


class SpeechCatalogTests(TestCase):
    def provider(self, **changes):
        values = dict(name="Fictional engine", slug="fictional", task="stt", engine="fixture",
                      languages=["en", "pl"], architecture="arm64")
        return SpeechProvider(**(values | changes))

    def test_metadata_validation_and_empty_catalog(self):
        self.assertFalse(SpeechProvider.objects.exists())
        self.provider().full_clean()
        self.provider(image="registry.example.test/team/engine@sha256:" + "a" * 64).full_clean()
        for changes in (
            {"languages": []}, {"languages": ["de"]}, {"languages": ["en", "en"]},
            {"image": "engine:latest"}, {"image": "user:password@host/image"},
            {"cpu_limit": Decimal("0")}, {"cpu_limit": Decimal("8.01")},
            {"memory_mib": 63}, {"memory_mib": 16385},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.provider(**changes).full_clean()

    def payload(self, language="en"):
        return dict(name="Fictional engine", slug="fictional", task="stt", engine="fixture",
                    languages=["en"], architecture="arm64", cpu_limit="1.00", memory_mib="1024",
                    **{"speech_models-TOTAL_FORMS": "1", "speech_models-INITIAL_FORMS": "0",
                       "speech_models-MIN_NUM_FORMS": "0", "speech_models-MAX_NUM_FORMS": "1000",
                       "speech_models-0-language": language, "speech_models-0-name": "Fixture model",
                       "speech_models-0-checksum": "b" * 64})

    def test_model_checksum_and_unsaved_parent_language(self):
        provider = self.provider(languages=["en"])
        with self.assertRaises(ValidationError):
            SpeechModel(provider=provider, language="pl", name="Fixture").clean()
        for checksum in ("wrong", "a" * 63, "g" * 64):
            with self.subTest(checksum=checksum), self.assertRaises(ValidationError):
                SpeechModel(provider=provider, language="en", name="Fixture", checksum=checksum).full_clean()

    def test_admin_permissions_and_inline_language(self):
        url = reverse("admin:model_providers_speechprovider_add")
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        staff.is_superuser = True
        staff.save()
        response = self.client.post(url, self.payload("pl"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SpeechProvider.objects.exists())
        response = self.client.post(url, self.payload())
        self.assertEqual(response.status_code, 302)
        provider = SpeechProvider.objects.get()
        self.assertEqual(provider.speech_models.get().language, "en")
        payload = self.payload()
        payload.update(languages=["pl"])
        payload.update({"speech_models-INITIAL_FORMS": "1",
                        "speech_models-0-id": provider.speech_models.get().pk,
                        "speech_models-0-provider": provider.pk})
        response = self.client.post(reverse("admin:model_providers_speechprovider_change", args=[provider.pk]), payload)
        self.assertEqual(response.status_code, 200)
        provider.refresh_from_db()
        self.assertEqual(provider.languages, ["en"])
        staff.is_superuser = False
        staff.save()
        staff.user_permissions.add(Permission.objects.get(codename="change_speechprovider"))
        self.client.force_login(staff)
        response = self.client.post(reverse("admin:model_providers_speechprovider_change", args=[provider.pk]), payload)
        self.assertEqual(response.status_code, 200)
        provider.refresh_from_db()
        self.assertEqual(provider.languages, ["en"])
