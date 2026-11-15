from time import sleep

from django.core.management.base import BaseCommand, CommandError

from bookings.confirmations import configured_methods, dispatch_confirmations


class Command(BaseCommand):
    help = "Submit pending booking confirmations using the configured email and SMS providers."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Check for pending confirmations every 10 seconds.")

    def handle(self, *args, **options):
        methods = configured_methods()
        if not methods:
            raise CommandError("Configure an SMTP or Twilio SMS sender before starting this worker.")
        watch = options["watch"]
        while True:
            accepted, failed = dispatch_confirmations(methods)
            if accepted or failed or not watch:
                self.stdout.write(f"Confirmations accepted by provider: {accepted}. Unsuccessful attempts: {failed}.")
            if not watch:
                if failed:
                    raise CommandError("Inspect booking confirmations in Django admin for unsuccessful attempts.")
                return
            sleep(10)
