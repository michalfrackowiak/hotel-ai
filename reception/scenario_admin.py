from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Scenario, ScenarioStep, ScenarioTransition, ScenarioVersion
from .scenario import default_chat_text, default_voice_parts
from .scenario_editor import ARRIVAL_KEYS, VoicePartsField, _fields, _placeholder_help
from .scenario_step_settings import (
    CHAT_FIELD, MAX_CHAT_TEXT, VOICE_FIELD, prompt_fields, prompt_key, validate_step_configuration,
)


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
    fields = ("scenario", "number", "base_version", "created_at", "published_at", "graph_validation", "outcome_coverage")
    readonly_fields = ("created_at", "published_at", "graph_validation", "outcome_coverage")
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
            return tuple(field for field in self.fields if field not in {"graph_validation", "outcome_coverage"})
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
        return self._graph_diagnostics(obj)[0]

    @admin.display(description="Outcome coverage")
    def outcome_coverage(self, obj):
        return self._graph_diagnostics(obj)[1]

    def _graph_diagnostics(self, obj):
        from .scenario_validation import validate_scenario_graph, validate_scenario_outcomes

        if obj is None or obj.pk is None:
            return ("Save the version to review its graph.", "Save the version to check outcome coverage.")
        if not hasattr(obj, "_admin_graph_diagnostics"):
            structure = "The saved graph passed validation."
            coverage = "The saved graph covers required outcomes."
            try:
                validate_scenario_outcomes(obj)
            except ValidationError as outcome_error:
                try:
                    validate_scenario_graph(obj)
                except ValidationError as graph_error:
                    structure = " ".join(graph_error.messages)
                    coverage = "Resolve the saved graph validation errors first."
                else:
                    coverage = " ".join(outcome_error.messages)
            obj._admin_graph_diagnostics = (structure, coverage)
        return obj._admin_graph_diagnostics



class ScenarioStepDraftForm(forms.ModelForm):
    chat_text = forms.CharField(
        required=False, max_length=MAX_CHAT_TEXT, label="Chat wording", widget=forms.Textarea(attrs={"rows": 4}),
    )
    voice_parts = VoicePartsField(
        required=False, label="Voice segments",
        help_text="Segments play from top to bottom. Clear a row's text, pause and Listen setting to remove it.",
    )
    clear_overrides = forms.BooleanField(required=False, label="Clear all prompt overrides")

    class Meta:
        model = ScenarioStep
        fields = ("version", "key", "position")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        step = self.instance
        scenario = step.version.scenario if step.pk else None
        applicable_fields = prompt_fields(scenario.channel, step.key) if scenario else ()
        for name in (CHAT_FIELD, VOICE_FIELD):
            if name not in applicable_fields:
                self.fields.pop(name, None)
        if not step.pk or step.configuration == {}:
            self.fields.pop("clear_overrides", None)
        if not scenario or not applicable_fields:
            return
        configuration = step.configuration if isinstance(step.configuration, dict) else {}
        key = prompt_key(scenario.language, step.key)
        if chat := self.fields.get(CHAT_FIELD):
            default = default_chat_text(scenario.language, key)
            self.initial[CHAT_FIELD] = configuration.get(CHAT_FIELD, "")
            chat.help_text = "Leave blank to use default wording." + _placeholder_help(_fields([default]))
            chat.widget.attrs["placeholder"] = default
        if voice := self.fields.get(VOICE_FIELD):
            parts = configuration.get(VOICE_FIELD)
            if isinstance(parts, list) and all(isinstance(part, dict) for part in parts):
                self.initial[VOICE_FIELD] = parts
            voice.widget.allow_listening = key in ARRIVAL_KEYS
            defaults = default_voice_parts(scenario.language, key)
            voice.help_text += " Leave every row blank to use the default script."
            voice.help_text += _placeholder_help(_fields(part["text"] for part in defaults))

    def clean(self):
        data = super().clean()
        step = self.instance
        if not step.pk:
            return data
        editable = [name for name in (CHAT_FIELD, VOICE_FIELD) if name in self.fields]
        if data.get("clear_overrides"):
            for name in editable:
                self.errors.pop(name, None)
            step.configuration = {}
        elif editable and all(name in data for name in editable):
            if not isinstance(step.configuration, dict):
                raise ValidationError("Clear the stored prompt overrides before entering new content.")
            configuration = {
                **{name: value for name, value in step.configuration.items() if name not in editable},
                **{name: data[name] for name in editable if data[name]},
            }
            scenario = step.version.scenario
            validate_step_configuration(scenario.language, scenario.channel, step.key, configuration)
            step.configuration = configuration
        return data



@admin.register(ScenarioStep)
class ScenarioStepAdmin(ScenarioDraftAdmin):
    allow_draft_deletion = True
    form = ScenarioStepDraftForm
    fields = ("version", "key", "position")
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

    def get_fields(self, request, obj=None):
        if obj is None:
            return self.fields
        fields = (*self.fields, *prompt_fields(obj.version.scenario.channel, obj.key))
        if obj.configuration != {} and self.has_change_permission(request, obj):
            return (*fields, "clear_overrides")
        return fields

    def get_readonly_fields(self, request, obj=None):
        return ("version", "key") if obj is not None else ()

    @admin.display(description="Chat wording")
    def chat_text(self, obj):
        if isinstance(obj.configuration, dict):
            text = obj.configuration.get(CHAT_FIELD)
            if isinstance(text, str) and text:
                return text
        return "Default wording"

    @admin.display(description="Voice segments")
    def voice_parts(self, obj):
        if not isinstance(obj.configuration, dict):
            return "Default script"
        parts = obj.configuration.get(VOICE_FIELD)
        if not isinstance(parts, list) or not parts or not all(isinstance(part, dict) for part in parts):
            return "Default script"
        return " ".join(
            f"{number}. {part.get('text', '')} ({part.get('seconds', 0)} s{', listen' if part.get('listen') else ''})"
            for number, part in enumerate(parts, 1)
        )

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
