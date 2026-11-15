from django.db import models


class Conversation(models.Model):
    session = models.OneToOneField("sessions.Session", on_delete=models.CASCADE, primary_key=True)
    state = models.JSONField()


class StaffHandover(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    language = models.CharField(max_length=2, choices=(("pl", "Polish"), ("en", "English")))
    created_at = models.DateTimeField(auto_now_add=True)
    closed = models.BooleanField(default=False)


class HandoverMessage(models.Model):
    handover = models.ForeignKey(StaffHandover, on_delete=models.CASCADE, related_name="messages")
    staff = models.BooleanField(default=False)
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
