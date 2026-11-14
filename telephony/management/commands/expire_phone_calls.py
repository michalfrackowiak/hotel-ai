from django.core.management.base import BaseCommand

from telephony.calls import expire_calls


class Command(BaseCommand):
    help = "Redact expired phone booking drafts and prune expired call receipts."

    def handle(self, *args, **options):
        redacted, deleted = expire_calls()
        self.stdout.write(f"Redacted {redacted} expired draft(s) and removed {deleted} expired call receipt(s).")
