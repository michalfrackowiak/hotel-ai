from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from model_providers.control_status import ControlStatusError


ACTIONS = (
    ("switch", "Speech switch", "switch_speech_controller"),
    ("recovery", "Speech recovery", "recover_speech_controller"),
)


class SpeechActionGroupTests(TestCase):
    def setup_group(self, command, name):
        call_command(command, stdout=StringIO())
        return Group.objects.get(name=name)

    def test_setup_is_repeatable_and_only_grants_the_named_action(self):
        for action, name, permission in ACTIONS:
            with self.subTest(action=action):
                command = f"setup_speech_{action}_group"
                group = self.setup_group(command, name)
                self.assertEqual(group.pk, self.setup_group(command, name).pk)
                self.assertFalse(group.user_set.exists())
                self.assertEqual(list(group.permissions.values_list("content_type__app_label", "codename")), [
                    ("model_providers", permission),
                ])

    @override_settings(
        HOTEL_SPEECH_CONTROL_SWITCH_ENABLED=True, HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=True,
        HOTEL_SPEECH_CONTROL_HOST="fixture-host", HOTEL_SPEECH_CONTROL_GENERATION="",
        HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:8093", HOTEL_SPEECH_CONTROL_TOKEN="fixture-token" * 4,
    )
    def test_each_action_requires_monitoring_access_and_its_own_feature_flag(self):
        operator = self.setup_group("setup_speech_operator_group", "Speech operator")
        for action, name, _ in ACTIONS:
            with self.subTest(action=action):
                user = get_user_model().objects.create_user(username=f"fixture-{action}", is_staff=True)
                user.groups.add(self.setup_group(f"setup_speech_{action}_group", name))
                self.client.force_login(user)
                route = reverse(f"admin:model_providers_speechselection_{action}_prepare")
                other = "recovery" if action == "switch" else "switch"
                other_route = reverse(f"admin:model_providers_speechselection_{other}_prepare")
                flag = f"HOTEL_SPEECH_CONTROL_{action.upper()}_ENABLED"
                with patch(
                    f"model_providers.{action}_admin.fetch_control_status",
                    side_effect=ControlStatusError("configuration"),
                ) as fetch:
                    self.assertEqual(self.client.get(route).status_code, 403)
                    fetch.assert_not_called()
                    user.groups.add(operator)
                    self.assertEqual(self.client.get(route).status_code, 200)
                    fetch.assert_called_once_with()
                    self.assertEqual(self.client.get(other_route).status_code, 403)
                    self.assertEqual(self.client.post(other_route).status_code, 403)
                    with self.settings(**{flag: False}):
                        self.assertEqual(self.client.get(route).status_code, 403)
                        self.assertEqual(self.client.post(route).status_code, 403)
                    fetch.assert_called_once_with()
