from django.db import migrations, models
import django.db.models.deletion


SCENARIO = "reception_scenario"
VERSION = "reception_scenarioversion"
STEP = "reception_scenariostep"
TRANSITION = "reception_scenariotransition"
AUDIT = "reception_scenariopublication"

POINTER_EXISTS = f"""
    EXISTS (
        SELECT 1 FROM {VERSION} AS version
        WHERE version.id = NEW.published_version_id
        AND version.scenario_id = NEW.id AND version.published_at IS NOT NULL
    )
"""
BASE_EXISTS = f"""
    EXISTS (
        SELECT 1 FROM {VERSION} AS base
        JOIN {SCENARIO} AS shared ON shared.id = base.scenario_id
        JOIN {SCENARIO} AS derived ON derived.id = NEW.scenario_id
        WHERE base.id = NEW.base_version_id AND base.published_at IS NOT NULL
        AND shared.channel = 'shared' AND derived.channel <> 'shared' AND shared.language = derived.language
    )
"""
STEP_VERSION_SEALED = f"""
    EXISTS (SELECT 1 FROM {VERSION} AS version WHERE version.id = NEW.version_id AND version.published_at IS NOT NULL)
"""
TRANSITION_ENDPOINTS_DRAFT = f"""
    EXISTS (
        SELECT 1 FROM {STEP} AS source
        JOIN {STEP} AS target ON target.version_id = source.version_id
        JOIN {VERSION} AS version ON version.id = source.version_id
        WHERE source.id = NEW.source_id AND target.id = NEW.target_id AND version.published_at IS NULL
    )
"""
AUDIT_TARGET_SELECTED = f"""
    EXISTS (
        SELECT 1 FROM {SCENARIO} AS scenario
        JOIN {VERSION} AS version ON version.id = NEW.selected_version_id AND version.scenario_id = scenario.id
        WHERE scenario.id = NEW.scenario_id AND scenario.published_version_id = version.id
        AND version.published_at IS NOT NULL
    )
"""
AUDIT_PREVIOUS_SEALED = f"""
    EXISTS (
        SELECT 1 FROM {VERSION} AS version
        WHERE version.id = NEW.previous_version_id AND version.scenario_id = NEW.scenario_id
        AND version.published_at IS NOT NULL
    )
"""

TRIGGERS = (
    ("scenario_insert_scope", "INSERT", SCENARIO, "NEW.published_version_id IS NOT NULL", "New scenarios must be unselected."),
    (
        "scenario_update_scope", "UPDATE", SCENARIO,
        f"NEW.language IS NOT OLD.language OR NEW.channel IS NOT OLD.channel OR NEW.id IS NOT OLD.id "
        f"OR (NEW.published_version_id IS NOT OLD.published_version_id AND NOT ({POINTER_EXISTS}))",
        "Scenario scope is fixed and selection must be a sealed version in that scope.",
    ),
    (
        "scenario_insert_version", "INSERT", VERSION,
        f"NEW.published_at IS NOT NULL OR (NEW.base_version_id IS NOT NULL AND NOT ({BASE_EXISTS}))",
        "New versions must be drafts with a valid shared base.",
    ),
    (
        "scenario_update_version", "UPDATE", VERSION,
        f"OLD.published_at IS NOT NULL OR NEW.id IS NOT OLD.id OR NEW.scenario_id IS NOT OLD.scenario_id "
        f"OR NEW.number IS NOT OLD.number OR (NEW.base_version_id IS NOT NULL AND NOT ({BASE_EXISTS}))",
        "Published versions and version identity cannot change. Shared bases must be valid.",
    ),
    (
        "scenario_insert_step", "INSERT", STEP,
        f"{STEP_VERSION_SEALED} OR json_type(NEW.configuration) <> 'object'",
        "Steps need object settings and an unsealed version.",
    ),
    (
        "scenario_update_step", "UPDATE", STEP,
        f"NEW.id IS NOT OLD.id OR NEW.version_id IS NOT OLD.version_id OR {STEP_VERSION_SEALED} "
        "OR json_type(NEW.configuration) <> 'object'",
        "Steps in published versions and step ownership cannot change.",
    ),
    (
        "scenario_insert_transition", "INSERT", TRANSITION, f"NOT ({TRANSITION_ENDPOINTS_DRAFT})",
        "Transitions must connect steps in the same draft.",
    ),
    (
        "scenario_update_transition", "UPDATE", TRANSITION,
        f"NEW.id IS NOT OLD.id OR NEW.source_id IS NOT OLD.source_id OR NEW.target_id IS NOT OLD.target_id "
        f"OR NOT ({TRANSITION_ENDPOINTS_DRAFT})",
        "Published transitions and transition endpoints cannot change.",
    ),
    (
        "scenario_insert_audit", "INSERT", AUDIT,
        f"NOT ({AUDIT_TARGET_SELECTED}) OR (NEW.previous_version_id IS NOT NULL AND NOT ({AUDIT_PREVIOUS_SEALED}))",
        "Publication history must refer to sealed versions in its scope.",
    ),
    ("scenario_update_audit", "UPDATE", AUDIT, "1", "Scenario publication history cannot change."),
)


def create_trigger(name, event, table, condition, message):
    return f"""
        CREATE TRIGGER {name}
        BEFORE {event} ON {table}
        FOR EACH ROW WHEN {condition}
        BEGIN
            SELECT RAISE(ABORT, '{message}');
        END;
    """


class Migration(migrations.Migration):
    dependencies = [("reception", "0008_versioned_scenarios")]

    operations = [
        migrations.AlterModelOptions(
            name="scenario",
            options={"permissions": (("publish_scenario", "Can publish and roll back booking scenarios"),)},
        ),
        migrations.CreateModel(
            name="ScenarioPublication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_id", models.PositiveBigIntegerField()),
                ("action", models.CharField(choices=[("publish", "Publish"), ("rollback", "Rollback")], max_length=8)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("scenario", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="publication_history", to="reception.scenario",
                )),
                ("previous_version", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="reception.scenarioversion",
                )),
                ("selected_version", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="+", to="reception.scenarioversion",
                )),
            ],
            options={
                "ordering": ("-pk",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(action__in=("publish", "rollback")), name="valid_scenario_publication_action",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(previous_version=models.F("selected_version")),
                        name="scenario_publication_changes_selection",
                    ),
                    models.CheckConstraint(condition=models.Q(actor_id__gte=1), name="positive_scenario_publication_actor"),
                ],
            },
        ),
        # Keep SQL DELETE available for Django flush and recovery. ORM deletes of
        # sealed graph and history rows are guarded by model signals.
        migrations.RunSQL(
            sql=[create_trigger(*trigger) for trigger in TRIGGERS],
            reverse_sql=[f"DROP TRIGGER {trigger[0]};" for trigger in reversed(TRIGGERS)],
        ),
    ]
