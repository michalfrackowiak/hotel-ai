from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from reception.staff_groups import STAFF_ROLE_GRANTS


class Command(BaseCommand):
    help = "Report missing staff role groups and permission drift without changing access."

    def handle(self, *args, **options):
        observed = {}
        rows = Group.objects.filter(name__in=STAFF_ROLE_GRANTS).order_by().values_list(
            "name", "permissions__content_type__app_label", "permissions__content_type__model",
            "permissions__codename",
        )
        for name, app_label, model, codename in rows:
            grants = observed.setdefault(name, set())
            if codename is not None:
                grants.add((app_label, model, codename))

        for name, grants in STAFF_ROLE_GRANTS.items():
            if name not in observed:
                self.stdout.write(f"{name}: Missing")
                continue
            expected = set(grants)
            missing = len(expected - observed[name])
            extra = len(observed[name] - expected)
            if missing or extra:
                self.stdout.write(f"{name}: Drift (missing grants: {missing}, extra grants: {extra})")
            else:
                self.stdout.write(f"{name}: Matches")
