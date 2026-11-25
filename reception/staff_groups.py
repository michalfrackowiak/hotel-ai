from django.contrib.auth.models import Group, Permission
from django.core.management.base import CommandError
from django.db import transaction


@transaction.atomic
def setup_staff_group(group_name, grants):
    permission_ids = set()
    for app, model, codename in grants:
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
