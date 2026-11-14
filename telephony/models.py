from django.db import models


class PhoneCall(models.Model):
    call_sid = models.CharField(max_length=34, primary_key=True)
    state = models.JSONField()
    missed_turns = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
