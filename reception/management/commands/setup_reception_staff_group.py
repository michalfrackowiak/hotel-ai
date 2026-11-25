from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


GROUP_NAME = "Reception staff"
GRANTS = (
    ("reception", "staffhandover", "view_staffhandover"),
    ("reception", "staffhandover", "change_staffhandover"),
    ("reception", "handovermessage", "view_handovermessage"),
    ("bookings", "booking", "view_booking"),
    ("bookings", "bookingconfirmation", "view_bookingconfirmation"),
)


class Command(BaseCommand):
    help = "Create or verify the Reception staff permission group without assigning any accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        permission_ids = set()
        for app, model, codename in GRANTS:
            try:
                permission_ids.add(Permission.objects.values_list("pk", flat=True).get(
                    content_type__app_label=app, content_type__model=model, codename=codename,
                ))
            except Permission.DoesNotExist as error:
                raise CommandError(f"Missing permission {app}.{codename}. Apply Django migrations first.") from error

        group, _ = Group.objects.select_for_update().get_or_create(name=GROUP_NAME)
        existing = set(group.permissions.values_list("pk", flat=True))
        if existing - permission_ids:
            raise CommandError(f"{GROUP_NAME} has unexpected permissions. Review the existing group before setup.")
        if existing == permission_ids:
            self.stdout.write(f"{GROUP_NAME} already has the expected permissions.")
            return
        if group.user_set.exists():
            raise CommandError(f"{GROUP_NAME} has members and is missing permissions. Review the existing group before setup.")

        group.permissions.add(*(permission_ids - existing))
        self.stdout.write(f"{GROUP_NAME} configured. No accounts were assigned.")
