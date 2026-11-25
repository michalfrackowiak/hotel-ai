from django.contrib.auth.models import Group, Permission
from django.core.management.base import CommandError
from django.db import transaction


STAFF_ROLE_GRANTS = {
    "Reception staff": (
        ("reception", "staffhandover", "view_staffhandover"),
        ("reception", "staffhandover", "change_staffhandover"),
        ("reception", "handovermessage", "view_handovermessage"),
        ("bookings", "booking", "view_booking"),
        ("bookings", "bookingconfirmation", "view_bookingconfirmation"),
    ),
    "Parser reviewer": (
        ("reception", "parsersample", "view_parsersample"),
        ("reception", "parsersample", "change_parsersample"),
    ),
    "Scenario editor": (
        ("reception", "scenario", "view_scenario"),
        ("reception", "scenarioversion", "view_scenarioversion"),
        ("reception", "scenarioversion", "add_scenarioversion"),
        ("reception", "scenarioversion", "change_scenarioversion"),
        ("reception", "scenariostep", "view_scenariostep"),
        ("reception", "scenariostep", "add_scenariostep"),
        ("reception", "scenariostep", "change_scenariostep"),
        ("reception", "scenariostep", "delete_scenariostep"),
        ("reception", "scenariotransition", "view_scenariotransition"),
        ("reception", "scenariotransition", "add_scenariotransition"),
        ("reception", "scenariotransition", "change_scenariotransition"),
        ("reception", "scenariotransition", "delete_scenariotransition"),
    ),
    "Scenario publisher": (
        ("reception", "scenario", "view_scenario"),
        ("reception", "scenario", "publish_scenario"),
        ("reception", "scenarioversion", "view_scenarioversion"),
        ("reception", "scenariostep", "view_scenariostep"),
        ("reception", "scenariotransition", "view_scenariotransition"),
    ),
    "Speech operator": (
        ("model_providers", "speechdeployment", "view_speechdeployment"),
        ("model_providers", "speechmodel", "view_speechmodel"),
        ("model_providers", "speechprovider", "view_speechprovider"),
        ("model_providers", "speechselection", "view_speechselection"),
    ),
    "Speech switch": (
        ("model_providers", "speechswitchrequest", "switch_speech_controller"),
    ),
    "Speech recovery": (
        ("model_providers", "speechrecoveryrequest", "recover_speech_controller"),
    ),
}


@transaction.atomic
def setup_staff_group(group_name):
    permission_ids = set()
    for app, model, codename in STAFF_ROLE_GRANTS[group_name]:
        try:
            permission_ids.add(Permission.objects.values_list("pk", flat=True).get(
                content_type__app_label=app, content_type__model=model, codename=codename,
            ))
        except Permission.DoesNotExist as error:
            raise CommandError(f"Missing permission {app}.{codename}. Apply Django migrations first.") from error

    group, _ = Group.objects.select_for_update().get_or_create(name=group_name)
    existing = set(group.permissions.values_list("pk", flat=True))
    if existing - permission_ids:
        raise CommandError(f"{group_name} has unexpected permissions. Review the existing group before setup.")
    if existing == permission_ids:
        return f"{group_name} already has the expected permissions."
    if group.user_set.exists():
        raise CommandError(f"{group_name} has members and is missing permissions. Review the existing group before setup.")

    group.permissions.add(*(permission_ids - existing))
    return f"{group_name} configured. No accounts were assigned."
