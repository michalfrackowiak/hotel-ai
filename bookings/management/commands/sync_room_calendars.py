from time import sleep

from django.core.management.base import BaseCommand, CommandError

from bookings.calendar_sync import CalendarError, GoogleCalendarClient, due_booking_ids, publish_due_bookings


class Command(BaseCommand):
    help = "Publish confirmed, current and upcoming bookings to their configured room calendars."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Check for pending bookings every 30 seconds.")

    def handle(self, *args, **options):
        watch = options["watch"]
        while True:
            try:
                published, failed = self.sync_once()
            except CalendarError as error:
                if not watch:
                    raise CommandError(str(error)) from error
                self.stderr.write(str(error))
            else:
                if published or failed or not watch:
                    self.stdout.write(f"Calendar bookings published: {published}. Failed attempts: {failed}.")
                if failed and not watch:
                    raise CommandError("Inspect calendar publications in Django admin for failure details.")

            if not watch:
                return
            sleep(30)

    def sync_once(self):
        booking_ids = due_booking_ids()
        if not booking_ids:
            return 0, 0
        with GoogleCalendarClient.connect() as client:
            return publish_due_bookings(client, booking_ids)
