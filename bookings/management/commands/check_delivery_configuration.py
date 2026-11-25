from django.core.management.base import BaseCommand, CommandError

from bookings.confirmations import configured_methods
from bookings.models import BookingConfirmation
from telephony.calls import staff_transfer_destination


class Command(BaseCommand):
    help = "Report confirmation sender and staff transfer settings for the current process."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require", action="append", choices=("email", "sms", "transfer"), default=[],
            help="Exit unsuccessfully if this channel is unconfigured. May be repeated.",
        )

    def handle(self, *args, **options):
        methods = configured_methods()
        self.stdout.write("Delivery configuration for this process. Provider connectivity is untested.")
        channels = (
            ("email", "SMTP sender", BookingConfirmation.Method.EMAIL in methods),
            ("sms", "SMS sender", BookingConfirmation.Method.SMS in methods),
            ("transfer", "Staff transfer destination", bool(staff_transfer_destination())),
        )
        required = set(options["require"])
        missing = []
        for channel, label, configured in channels:
            self.stdout.write(f"{label}: {'Configured' if configured else 'Not configured'}")
            if channel in required and not configured:
                missing.append(label)
        if missing:
            raise CommandError(f"Required delivery configuration missing: {', '.join(missing)}.")
