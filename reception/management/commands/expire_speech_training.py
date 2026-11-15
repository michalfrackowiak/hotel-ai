from django.core.management.base import BaseCommand

from reception.speech_training import expire_speech_training


class Command(BaseCommand):
    help = "Delete speech training samples past their retention period."

    def handle(self, *args, **options):
        self.stdout.write(f"Removed {expire_speech_training()} expired speech training sample(s).")
