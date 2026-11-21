from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Scenario, ScenarioStep, ScenarioTransition, ScenarioVersion


class ScenarioDraftAdmin(admin.ModelAdmin):
    actions = None
    allow_draft_deletion = False

    def has_delete_permission(self, request, obj=None):
        # Rechecked inside Django's atomic POST delete view.
        return (
            self.allow_draft_deletion
            and obj is not None
            and self.scenario_version(obj).published_at is None
            and super().has_delete_permission(request, obj)
        )

    def has_change_permission(self, request, obj=None):
        # Django wraps POST change forms in a write transaction. The SQLite
        # IMMEDIATE mode serializes this check and the save with publication.
        if obj is not None and self.scenario_version(obj).published_at is not None:
            return False
        return super().has_change_permission(request, obj)


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    fields = ("language", "channel", "published_version")
    readonly_fields = ("published_version",)
    list_display = ("language", "channel", "published_version")
    list_filter = ("language", "channel")
    list_select_related = ("published_version__scenario",)
    ordering = ("language", "channel")
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return self.fields if obj is not None else self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ScenarioVersion)
class ScenarioVersionAdmin(ScenarioDraftAdmin):
    fields = ("scenario", "number", "base_version", "created_at", "published_at", "graph_validation")
    readonly_fields = ("created_at", "published_at", "graph_validation")
    list_display = ("scenario", "number", "base_version", "created_at", "published_at")
    list_filter = ("scenario__language", "scenario__channel")
    list_select_related = ("scenario", "base_version__scenario")
    ordering = ("scenario__language", "scenario__channel", "-number")

    def scenario_version(self, obj):
        return obj

    def get_fields(self, request, obj=None):
        user = request.user
        can_inspect_graph = (
            (user.has_perm("reception.view_scenariostep") or user.has_perm("reception.change_scenariostep"))
            and (
                user.has_perm("reception.view_scenariotransition")
                or user.has_perm("reception.change_scenariotransition")
            )
        )
        if not can_inspect_graph:
            return tuple(field for field in self.fields if field != "graph_validation")
        return self.fields

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return (*self.readonly_fields, "scenario", "number")
        return self.readonly_fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "base_version":
            kwargs["queryset"] = ScenarioVersion.objects.filter(
                scenario__channel="shared", published_at__isnull=False,
            ).select_related("scenario")
            kwargs["help_text"] = "Select a published shared version in the same language or leave this blank."
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Saved graph validation")
    def graph_validation(self, obj):
        from .scenario_validation import validate_scenario_graph

        if obj is None or obj.pk is None:
            return "Save the version to review its graph."
        try:
            validate_scenario_graph(obj)
        except ValidationError as error:
            return " ".join(error.messages)
        return "The saved graph passed validation."


@admin.register(ScenarioStep)
class ScenarioStepAdmin(ScenarioDraftAdmin):
    allow_draft_deletion = True
    fields = ("version", "key", "position", "configuration")
    list_display = ("version", "key", "position")
    list_filter = ("version__scenario__language", "version__scenario__channel")
    list_select_related = ("version__scenario",)
    search_fields = ("key",)

    def scenario_version(self, obj):
        return obj.version

    def has_delete_permission(self, request, obj=None):
        return (
            super().has_delete_permission(request, obj)
            and not obj.incoming_transitions.exists()
            and not obj.outgoing_transitions.exists()
        )

    def get_readonly_fields(self, request, obj=None):
        return ("version",) if obj is not None else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "version":
            kwargs["queryset"] = ScenarioVersion.objects.filter(published_at__isnull=True).select_related("scenario")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class DraftStepChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.version} · {obj.key}"


@admin.register(ScenarioTransition)
class ScenarioTransitionAdmin(ScenarioDraftAdmin):
    allow_draft_deletion = True
    fields = ("source", "event", "target")
    list_display = ("version", "source", "event", "target")
    list_filter = ("source__version__scenario__language", "source__version__scenario__channel")
    list_select_related = ("source__version__scenario", "target")
    search_fields = ("source__key", "event", "target__key")

    def scenario_version(self, obj):
        return obj.source.version

    @admin.display(description="Scenario version")
    def version(self, obj):
        return obj.source.version

    def get_readonly_fields(self, request, obj=None):
        return ("source", "target") if obj is not None else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in {"source", "target"}:
            kwargs["form_class"] = DraftStepChoiceField
            kwargs["queryset"] = ScenarioStep.objects.filter(
                version__published_at__isnull=True,
            ).select_related("version__scenario").order_by(
                "version__scenario__language", "version__scenario__channel", "-version__number", "position", "key",
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
