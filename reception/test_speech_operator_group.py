from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from model_providers.models import SpeechSelection


class SpeechOperatorGroupTests(TestCase):
    def setup_group(self):
        call_command("setup_speech_operator_group", stdout=StringIO())
        return Group.objects.get(name="Speech operator")

    def test_setup_grants_only_speech_view_permissions(self):
        group = self.setup_group()
        self.assertFalse(group.user_set.exists())
        self.assertEqual(group.pk, self.setup_group().pk)
        self.assertEqual(set(group.permissions.values_list("content_type__app_label", "codename")), {
            ("model_providers", "view_speechdeployment"),
            ("model_providers", "view_speechmodel"),
            ("model_providers", "view_speechprovider"),
            ("model_providers", "view_speechselection"),
        })

    @override_settings(
        HOTEL_SPEECH_CONTROL_URL="", HOTEL_SPEECH_CONTROLLER_URL="",
        HOTEL_SPEECH_CONTROL_SWITCH_ENABLED=True, HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=True,
    )
    def test_role_can_inspect_speech_but_cannot_change_it(self):
        user = get_user_model().objects.create_user(username="speech-fixture", is_staff=True)
        user.groups.add(self.setup_group())
        self.client.force_login(user)
        for route in (
            "speechprovider_changelist", "speechdeployment_changelist", "speechselection_changelist",
            "speechselection_controller_status", "speechselection_inventory", "speechselection_preflight",
            "speechselection_transition",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(f"admin:model_providers_{route}")).status_code, 200)

        selection = SpeechSelection.objects.get(language="en", task="stt")
        detail = reverse("admin:model_providers_speechselection_change", args=[selection.pk])
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"deployment": ""}).status_code, 403)

        for route in ("speechselection_switch_prepare", "speechselection_recovery_prepare"):
            with self.subTest(route=route):
                url = reverse(f"admin:model_providers_{route}")
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)

        for route in (
            "model_providers_speechprovider_add", "model_providers_speechdeployment_add",
            "model_providers_agentconfiguration_changelist", "reception_parsersample_changelist",
            "reception_staffhandover_changelist", "reception_scenarioversion_changelist",
            "bookings_booking_changelist", "auth_user_changelist",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(f"admin:{route}")).status_code, 403)
