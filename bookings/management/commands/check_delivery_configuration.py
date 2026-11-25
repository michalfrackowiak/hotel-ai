from django.core.management.base import BaseCommand

from bookings.confirmations import configured_methods
from bookings.models import BookingConfirmation
from telephony.calls import staff_transfer_destination


class Command(BaseCommand):
    help = "Report confirmation sender and staff transfer settings for the current process."

    def handle(self, *args, **options):
        methods = configured_methods()
        self.stdout.write("Delivery configuration for this process. Provider connectivity is untested.")
        for label, configured in (
            ("SMTP sender", BookingConfirmation.Method.EMAIL in methods),
            ("SMS sender", BookingConfirmation.Method.SMS in methods),
            ("Staff transfer destination", bool(staff_transfer_destination())),
        ):
            self.stdout.write(f"{label}: {'Configured' if configured else 'Not configured'}")
