from django.contrib.sessions.management.commands.clearsessions import Command as DjangoCommand

from reception.session_continuity import delete_expired_conversations


class Command(DjangoCommand):
    help = "Clean out expired Django sessions and a bounded batch of reception conversations."

    def handle(self, **options):
        super().handle(**options)
        delete_expired_conversations()
