from django.db import models


class GuestAccount(models.Model):
    google_subject = models.CharField(max_length=255, unique=True)


class GoogleLoginChallenge(models.Model):
    session = models.OneToOneField("sessions.Session", on_delete=models.CASCADE, primary_key=True)
    nonce = models.CharField(max_length=43)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
