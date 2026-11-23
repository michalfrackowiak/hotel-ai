from django.db import migrations, models


def copy_expiry(apps, schema_editor):
    conversation = apps.get_model("reception", "Conversation")
    session = apps.get_model("sessions", "Session")
    alias = schema_editor.connection.alias
    expiry = session.objects.using(alias).filter(pk=models.OuterRef("pk")).values("expire_date")
    conversation.objects.using(alias).update(expires_at=models.Subquery(expiry))


class Migration(migrations.Migration):
    dependencies = [("reception", "0009_scenario_publication")]

    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="session",
            field=models.CharField(db_column="session_id", max_length=40, primary_key=True, serialize=False),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField("conversation", "session", "conversation_id"),
            ],
        ),
        migrations.AddField(
            model_name="conversation",
            name="expires_at",
            field=models.DateTimeField(db_index=True, null=True),
        ),
        migrations.RunPython(copy_expiry, migrations.RunPython.noop),
    ]
