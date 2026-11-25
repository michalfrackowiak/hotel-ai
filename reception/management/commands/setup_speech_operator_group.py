from django.core.management.base import BaseCommand

from reception.staff_groups import setup_staff_group


GRANTS = (
    ("model_providers", "speechdeployment", "view_speechdeployment"),
    ("model_providers", "speechmodel", "view_speechmodel"),
    ("model_providers", "speechprovider", "view_speechprovider"),
    ("model_providers", "speechselection", "view_speechselection"),
)


class Command(BaseCommand):
    help = "Create or verify the read-only Speech operator permission group without assigning any accounts."

    def handle(self, *args, **options):
        return setup_staff_group("Speech operator", GRANTS)
