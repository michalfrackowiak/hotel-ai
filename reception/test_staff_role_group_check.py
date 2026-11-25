from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase


class StaffRoleGroupCheckTests(TestCase):
    def test_checks_all_builtin_groups_without_revealing_account_details_or_changing_grants(self):
        call_command("setup_parser_reviewer_group", stdout=StringIO())
        call_command("setup_speech_operator_group", stdout=StringIO())
        operator = Group.objects.get(name="Speech operator")
        user = get_user_model().objects.create_user(username="fictional-operator")
        operator.user_set.add(user)
        operator.permissions.remove(operator.permissions.get(codename="view_speechdeployment"))
        operator.permissions.add(Permission.objects.get(
            content_type__app_label="auth", codename="change_user",
        ))
        Group.objects.create(name="Speech switch")
        Group.objects.create(name="Fictional unrelated group")

        prior_grants = set(operator.permissions.values_list("pk", flat=True))
        output = StringIO()
        with self.assertNumQueries(1):
            call_command("check_staff_role_groups", stdout=output)
        self.assertEqual(output.getvalue().splitlines(), [
            "Reception staff: Missing",
            "Parser reviewer: Matches",
            "Scenario editor: Missing",
            "Scenario publisher: Missing",
            "Speech operator: Drift (missing grants: 1, extra grants: 1)",
            "Speech switch: Drift (missing grants: 1, extra grants: 0)",
            "Speech recovery: Missing",
        ])
        self.assertEqual(set(operator.permissions.values_list("pk", flat=True)), prior_grants)
        self.assertTrue(operator.user_set.filter(pk=user.pk).exists())
        self.assertFalse(Group.objects.filter(name="Reception staff").exists())
