from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reception", "0010_conversation_session_continuity")]

    operations = [
        migrations.AddField(
            model_name="parsersample",
            name="correction_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
