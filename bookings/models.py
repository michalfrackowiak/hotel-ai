from uuid import uuid4

from django.db import models


CURRENCY = "PLN"
RATE_DIGITS = 8
RATE_PLACES = 2


class Room(models.Model):
    code = models.CharField(max_length=40, unique=True)
    capacity = models.PositiveSmallIntegerField()
    google_calendar_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(capacity__gt=0), name="room_capacity_positive")]

    def __str__(self):
        return self.code


class Price(models.Model):
    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name="price")
    nightly_rate = models.DecimalField(max_digits=RATE_DIGITS, decimal_places=RATE_PLACES)

    class Meta:
        verbose_name_plural = "prices"
        constraints = [models.CheckConstraint(condition=models.Q(nightly_rate__gt=0), name="price_rate_positive")]

    def __str__(self):
        return f"{self.room} · {self.nightly_rate:.2f} {CURRENCY}"


class Client(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["name", "email"], name="client_contact_unique")]

    def __str__(self):
        return self.name


class Booking(models.Model):
    reference = models.UUIDField(default=uuid4, unique=True, editable=False)
    idempotency_key = models.UUIDField(unique=True, editable=False)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="bookings")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="bookings")
    check_in = models.DateField()
    check_out = models.DateField()
    guests = models.PositiveSmallIntegerField()
    nightly_rate = models.DecimalField(max_digits=RATE_DIGITS, decimal_places=RATE_PLACES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["room", "check_in", "check_out"], name="booking_room_stay_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(check_out__gt=models.F("check_in")), name="booking_dates_ordered"),
            models.CheckConstraint(condition=models.Q(guests__gt=0), name="booking_guests_positive"),
            models.CheckConstraint(condition=models.Q(nightly_rate__gt=0), name="booking_rate_positive"),
        ]

    @property
    def total_price(self):
        return self.nightly_rate * (self.check_out - self.check_in).days

    def __str__(self):
        return str(self.reference)


class CalendarPublication(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="calendar_publication")
    calendar_id = models.CharField(max_length=255)
    event_may_exist = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return str(self.booking)
