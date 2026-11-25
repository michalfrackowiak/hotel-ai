from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from reception.staff_groups import STAFF_ROLE_GRANTS


class Command(BaseCommand):
    help = "Report missing staff role groups and permission drift without changing access."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require", action="append", choices=tuple(STAFF_ROLE_GRANTS), default=[],
            help="Exit unsuccessfully if this group is missing or has permission drift. May be repeated.",
        )

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

        required = set(options["require"])
        failed = []
        for name, grants in STAFF_ROLE_GRANTS.items():
            if name not in observed:
                self.stdout.write(f"{name}: Missing")
            else:
                expected = set(grants)
                missing = len(expected - observed[name])
                extra = len(observed[name] - expected)
                if not (missing or extra):
                    self.stdout.write(f"{name}: Matches")
                    continue
                self.stdout.write(f"{name}: Drift (missing grants: {missing}, extra grants: {extra})")
            if name in required:
                failed.append(name)
        if failed:
            raise CommandError(f"Required staff role groups are missing or have permission drift: {', '.join(failed)}.")
