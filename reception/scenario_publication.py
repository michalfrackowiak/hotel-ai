"""Internal atomic selection of previously validated scenario versions."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils import timezone

from .models import Scenario, ScenarioPublication, ScenarioVersion
from .scenario_materialization import materialize_scenario_defaults
from .scenario_validation import validate_materialized_scenario


class ScenarioSelectionConflict(ValidationError):
    """The selected scenario version changed since the caller reviewed it."""


def publish_scenario_version(scenario_id, version_id, *, expected_version_id, actor, using=DEFAULT_DB_ALIAS):
    """Materialize and seal a draft, select it, and record one audit event."""
    return _select_version(scenario_id, version_id, expected_version_id, actor, using, action="publish")


def rollback_scenario_version(scenario_id, version_id, *, expected_version_id, actor, using=DEFAULT_DB_ALIAS):
    """Select a prior sealed version from the same scope without changing its graph."""
    return _select_version(scenario_id, version_id, expected_version_id, actor, using, action="rollback")


def _select_version(scenario_id, version_id, expected_version_id, actor, using, *, action):
    connection = connections[using]
    if connection.vendor != "sqlite" or connection.settings_dict["OPTIONS"].get("transaction_mode") != "IMMEDIATE":
        raise ValidationError("Scenario publication requires the SQLite IMMEDIATE write transaction.")

    actor_id = getattr(actor, "pk", None)
    if actor_id is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Scenario publication requires an active staff actor.")

    with transaction.atomic(using=using):
        staff = get_user_model().objects.using(using).filter(pk=actor_id, is_active=True, is_staff=True).first()
        if staff is None or not staff.has_perm("reception.publish_scenario"):
            raise PermissionDenied("The staff actor does not have permission to publish scenarios.")

        scenario = Scenario.objects.using(using).filter(pk=scenario_id).first()
        if scenario is None:
            raise ValidationError("The scenario no longer exists.")
        if scenario.published_version_id != expected_version_id:
            raise ScenarioSelectionConflict("The selected version changed. Review the current version and try again.")

        versions = ScenarioVersion.objects.using(using).filter(scenario_id=scenario.pk)
        version = versions.filter(pk=version_id).first()
        if version is None:
            raise ValidationError("Select a version belonging to this scenario.")
        if version.pk == expected_version_id:
            raise ValidationError("The requested version is already selected.")

        if action == "publish":
            if version.published_at is not None:
                raise ValidationError("Select a draft version to publish.")
            materialize_scenario_defaults(version)
            validate_materialized_scenario(version)
            sealed = versions.filter(pk=version.pk, published_at__isnull=True).update(published_at=timezone.now())
            if sealed != 1:
                raise ScenarioSelectionConflict("The draft changed before it could be sealed.")
        elif version.published_at is None or not ScenarioPublication.objects.using(using).filter(
            selected_version_id=version.pk, action="publish",
        ).exists():
            raise ValidationError("Select a previously published version to roll back to.")
        else:
            validate_materialized_scenario(version)

        selected = Scenario.objects.using(using).filter(
            pk=scenario.pk, published_version_id=expected_version_id,
        ).update(published_version_id=version.pk)
        if selected != 1:
            raise ScenarioSelectionConflict("The selected version changed before the operation completed.")

        return ScenarioPublication.objects.using(using).create(
            scenario_id=scenario.pk,
            previous_version_id=expected_version_id,
            selected_version_id=version.pk,
            actor_id=actor_id,
            action=action,
        )
