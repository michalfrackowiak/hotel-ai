from django.core.management.base import BaseCommand

from reception.staff_groups import setup_staff_group


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

    def handle(self, *args, **options):
        return setup_staff_group(GROUP_NAME, GRANTS)
