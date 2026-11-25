from django.core.management.base import BaseCommand

from reception.staff_groups import setup_staff_group


GRANTS = (
    ("reception", "scenario", "view_scenario"),
    ("reception", "scenario", "publish_scenario"),
    ("reception", "scenarioversion", "view_scenarioversion"),
    ("reception", "scenariostep", "view_scenariostep"),
    ("reception", "scenariotransition", "view_scenariotransition"),
)


class Command(BaseCommand):
    help = "Create or verify the Scenario publisher permission group without assigning any accounts."

    def handle(self, *args, **options):
        return setup_staff_group("Scenario publisher", GRANTS)
