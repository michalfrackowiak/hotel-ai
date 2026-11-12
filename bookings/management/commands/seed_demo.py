import os
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookings.models import Price, Room


class Command(BaseCommand):
    help = "Seed the two demo rooms and optionally create the locally configured demo administrator."

    def handle(self, *args, **options):
        username = os.environ.get("HOTEL_DEMO_ADMIN_USERNAME")
        password = os.environ.get("HOTEL_DEMO_ADMIN_PASSWORD")
        if bool(username) != bool(password):
            raise CommandError("Set both HOTEL_DEMO_ADMIN_USERNAME and HOTEL_DEMO_ADMIN_PASSWORD to seed the administrator.")

        with transaction.atomic():
            for code, capacity, rate in (("ROOM-2", 2, "120.00"), ("ROOM-6", 6, "200.00")):
                room, _ = Room.objects.get_or_create(code=code, defaults={"capacity": capacity})
                Price.objects.get_or_create(room=room, defaults={"nightly_rate": Decimal(rate)})

            if username and password:
                users = get_user_model().objects
                existing = users.filter(username=username).first()
                if existing is None:
                    users.create_superuser(username=username, password=password)
                elif not existing.is_superuser or not existing.is_staff or not existing.is_active:
                    raise CommandError("The configured demo username belongs to an account without administrator access.")

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
