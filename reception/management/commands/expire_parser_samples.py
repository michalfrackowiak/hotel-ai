from django.core.management.base import BaseCommand

from reception.parser_review import delete_expired_samples


class Command(BaseCommand):
    help = "Delete expired parser training samples."

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {delete_expired_samples()} expired parser training samples.")
