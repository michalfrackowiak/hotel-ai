from django.db import migrations


def seed_contact_prompts(apps, schema_editor):
    prompts = apps.get_model("reception", "ScenarioPrompt").objects.using(schema_editor.connection.alias)
    for language in ("en", "pl"):
        for key in ("confirm_email", "confirm_phone"):
            prompts.get_or_create(language=language, key=key)


def remove_contact_prompts(apps, schema_editor):
    prompts = apps.get_model("reception", "ScenarioPrompt").objects.using(schema_editor.connection.alias)
    prompts.filter(key__in=("confirm_email", "confirm_phone")).delete()


class Migration(migrations.Migration):
    dependencies = [("reception", "0006_parserconfiguration_delete_speechtrainingsample")]
    operations = [migrations.RunPython(seed_contact_prompts, remove_contact_prompts)]
