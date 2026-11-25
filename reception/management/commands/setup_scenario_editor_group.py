from django.core.management.base import BaseCommand

from reception.staff_groups import setup_staff_group


GRANTS = (
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
)


class Command(BaseCommand):
    help = "Create or verify the Scenario editor permission group without assigning any accounts."

    def handle(self, *args, **options):
        return setup_staff_group("Scenario editor", GRANTS)
