from django.db import models


class Conversation(models.Model):
    session = models.OneToOneField("sessions.Session", on_delete=models.CASCADE, primary_key=True)
    state = models.JSONField()
