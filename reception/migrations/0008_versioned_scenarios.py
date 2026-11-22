from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("reception", "0007_contact_confirmation"),
    ]

    operations = [
        migrations.CreateModel(
            name="Scenario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language", models.CharField(choices=[("pl", "Polish"), ("en", "English")], max_length=2)),
                ("channel", models.CharField(
                    choices=[("chat", "Chat"), ("voice", "Voice"), ("shared", "Shared defaults")], max_length=6,
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("language", "channel"), name="unique_scenario_scope"),
                    models.CheckConstraint(condition=models.Q(language__in=("pl", "en")), name="valid_scenario_language"),
                    models.CheckConstraint(
                        condition=models.Q(channel__in=("chat", "voice", "shared")), name="valid_scenario_channel",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScenarioVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("published_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("base_version", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="derived_versions", to="reception.scenarioversion",
                )),
                ("scenario", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="reception.scenario",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("scenario", "number"), name="unique_scenario_version_number"),
                    models.CheckConstraint(condition=models.Q(number__gte=1), name="positive_scenario_version_number"),
                ],
            },
        ),
        migrations.AddField(
            model_name="scenario",
            name="published_version",
            field=models.ForeignKey(
                blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="+", to="reception.scenarioversion",
            ),
        ),
        migrations.CreateModel(
            name="ScenarioStep",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=48)),
                ("position", models.PositiveIntegerField(default=0)),
                ("configuration", models.JSONField(blank=True, default=dict)),
                ("version", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="steps", to="reception.scenarioversion",
                )),
            ],
            options={
                "ordering": ("position", "key"),
                "constraints": [
                    models.UniqueConstraint(fields=("version", "key"), name="unique_scenario_step_key"),
                    models.CheckConstraint(condition=~models.Q(key=""), name="nonempty_scenario_step_key"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScenarioTransition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.SlugField(max_length=48)),
                ("source", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="outgoing_transitions", to="reception.scenariostep",
                )),
                ("target", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="incoming_transitions", to="reception.scenariostep",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("source", "event"), name="unique_scenario_transition_event"),
                    models.CheckConstraint(condition=~models.Q(event=""), name="nonempty_scenario_transition_event"),
                ],
            },
        ),
    ]
