from django.core.management.base import BaseCommand

from reception.staff_groups import setup_staff_group


class Command(BaseCommand):
    help = "Create or verify the Speech recovery permission group without assigning any accounts."

    def handle(self, *args, **options):
        return setup_staff_group("Speech recovery")
