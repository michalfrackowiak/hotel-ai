from django import forms
from django.contrib import admin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods

from .models import Scenario, ScenarioPublication, ScenarioStep, ScenarioTransition, ScenarioVersion
from .scenario import default_chat_text, default_voice_parts
from .scenario_answers import MAX_ANSWER_LABEL, ROOM_ANSWER_LABEL, answer_label_defaults, answer_label_fields
from .scenario_editor import ARRIVAL_KEYS, VoicePartsField, _fields, _placeholder_help
from .scenario_step_settings import (
    ARRIVAL_INPUT_CHOICES, ARRIVAL_INPUT_FIELD, CHAT_FIELD, MAX_CHAT_TEXT, MAX_RETRY_LIMIT, MAX_SILENCE_SECONDS,
    OUTCOME_PROMPTS, PARSER_FALLBACK_CHOICES, PARSER_FALLBACK_FIELD, PARSER_FALLBACK_LABELS, RETRY_LIMIT_FIELD, SILENCE_WINDOW_FIELD, VOICE_FIELD, VOICE_INTERRUPTION_CHOICES,
    VOICE_INTERRUPTION_FIELD, VOICE_INTERRUPTION_LABELS, VOICE_TIMEOUT_CHOICES, VOICE_TIMEOUT_FIELD,
    VOICE_TIMEOUT_LABELS, outcome_field, parser_fallback_mode, prompt_bindings, prompt_fields,
    silence_window_seconds, validate_step_configuration, voice_interruption_mode, voice_timeout_mode,
)

SELECTION_SALT = "reception.scenario-selection.v1"
SELECTION_MAX_AGE = 300


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
    change_form_template = "admin/reception/scenarioversion/change_form.html"
    fields = ("scenario", "number", "base_version", "created_at", "published_at", "graph_validation", "outcome_coverage")
    readonly_fields = ("created_at", "published_at", "graph_validation", "outcome_coverage")
    list_display = ("scenario", "number", "base_version", "created_at", "published_at")
    list_filter = ("scenario__language", "scenario__channel")
    list_select_related = ("scenario", "base_version__scenario")
    ordering = ("scenario__language", "scenario__channel", "-number")

    def scenario_version(self, obj):
        return obj

    @staticmethod
    def _can_inspect_graph(user):
        return (
            (user.has_perm("reception.view_scenariostep") or user.has_perm("reception.change_scenariostep"))
            and (
                user.has_perm("reception.view_scenariotransition")
                or user.has_perm("reception.change_scenariotransition")
            )
        )

    def _can_review_selection(self, request, obj=None):
        user = request.user
        return (
            user.has_perm("reception.publish_scenario")
            and self.has_view_or_change_permission(request, obj)
            and (user.has_perm("reception.view_scenario") or user.has_perm("reception.change_scenario"))
            and self._can_inspect_graph(user)
        )

    def get_fields(self, request, obj=None):
        if not self._can_inspect_graph(request.user):
            return tuple(field for field in self.fields if field not in {"graph_validation", "outcome_coverage"})
        return self.fields

    def _can_view_flow(self, request, obj=None):
        return self.has_view_or_change_permission(request, obj) and self._can_inspect_graph(request.user)

    def get_urls(self):
        return [
            path(
                "<int:object_id>/flow/",
                self.admin_site.admin_view(self.flow_overview),
                name="reception_scenarioversion_flow",
            ),
            path(
                "<int:object_id>/selection/<str:action>/",
                self.admin_site.admin_view(self.selection_review),
                name="reception_scenarioversion_selection",
            ),
            *super().get_urls(),
        ]

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None and self._can_view_flow(request, obj):
            context["scenario_flow_url"] = reverse(
                f"{self.admin_site.name}:reception_scenarioversion_flow", args=[obj.pk],
            )
        if obj is not None and self._can_review_selection(request, obj):
            action = "rollback" if obj.published_at is not None else "publish"
            context["scenario_selection_url"] = reverse(
                f"{self.admin_site.name}:reception_scenarioversion_selection", args=[obj.pk, action],
            )
            active = obj.scenario.published_version_id == obj.pk
            context["scenario_selection_label"] = "Selection history" if active else f"Review {action}"
        return super().render_change_form(request, context, add, change, form_url, obj)

    @method_decorator(require_http_methods(["GET", "POST"]))
    def flow_overview(self, request, object_id):
        from .scenario_overview import scenario_flow_overview
        from .scenario_simulator import BookingReplySimulationForm

        if not self._can_view_flow(request):
            raise PermissionDenied
        version = self.get_object(request, object_id)
        if version is None:
            raise Http404
        if not self._can_view_flow(request, version):
            raise PermissionDenied

        request.current_app = self.admin_site.name
        return TemplateResponse(request, "admin/reception/scenarioversion/flow_overview.html", {
            **self.admin_site.each_context(request),
            **scenario_flow_overview(
                version,
                simulation_form=BookingReplySimulationForm(
                    request.POST if request.method == "POST" else None, language=version.scenario.language,
                ),
            ),
            "title": "Scenario flow overview",
            "opts": self.model._meta,
            "version": version,
            "change_url": reverse(f"{self.admin_site.name}:reception_scenarioversion_change", args=[version.pk]),
        })

    @method_decorator(require_http_methods(["GET", "POST"]))
    def selection_review(self, request, object_id, action):
        from .scenario_publication import (
            ScenarioSelectionConflict, publish_scenario_version, review_scenario_selection, rollback_scenario_version,
        )

        operations = {"publish": publish_scenario_version, "rollback": rollback_scenario_version}
        if action not in operations:
            raise Http404
        if not self._can_review_selection(request):
            raise PermissionDenied
        version = self.get_object(request, object_id)
        if version is None:
            raise Http404
        if not self._can_review_selection(request, version):
            raise PermissionDenied

        scenario = version.scenario
        using = version._state.db
        identity = {
            "actor": request.user.pk,
            "scenario": scenario.pk,
            "version": version.pk,
            "action": action,
        }
        change_url = reverse(f"{self.admin_site.name}:reception_scenarioversion_change", args=[version.pk])
        error_messages = []
        status = 200
        if request.method == "POST":
            try:
                snapshot = signing.loads(
                    request.POST.get("review_token", ""), salt=SELECTION_SALT, max_age=SELECTION_MAX_AGE,
                )
                if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in identity.items()):
                    raise signing.BadSignature
                operations[action](
                    scenario.pk,
                    version.pk,
                    expected_version_id=snapshot["current"],
                    expected_audit_id=snapshot["audit"],
                    expected_fingerprint=snapshot["fingerprint"],
                    actor=request.user,
                    using=using,
                )
            except (signing.BadSignature, KeyError):
                error_messages = ["The review expired or does not match this operation. Review the current changes."]
                status = 409
            except ScenarioSelectionConflict as error:
                error_messages = error.messages
                status = 409
            except ValidationError as error:
                error_messages = error.messages
                status = 400
            else:
                message = "Scenario version published." if action == "publish" else "Previous scenario version selected."
                self.message_user(request, message)
                return HttpResponseRedirect(change_url)

        scenario.refresh_from_db(using=using, fields=["published_version"])
        current_id = scenario.published_version_id
        candidate_number = version.number
        base_number = None
        current_number = None
        review_token = None
        diff = ()
        active = current_id == version.pk
        if not active:
            try:
                review = review_scenario_selection(scenario.pk, version.pk, actor=request.user, action=action, using=using)
            except ValidationError as error:
                error_messages.extend(error.messages)
            else:
                candidate_number = review.candidate_number
                base_number = review.base_number
                current_number = review.current_number
                diff = review.diff
                review_token = signing.dumps({
                    **identity,
                    "current": review.expected_version_id,
                    "audit": review.expected_audit_id,
                    "fingerprint": review.fingerprint,
                }, salt=SELECTION_SALT)

        if review_token is None and current_id is not None:
            current_number = ScenarioVersion.objects.using(using).filter(
                pk=current_id, scenario_id=scenario.pk,
            ).values_list("number", flat=True).first()
        history = ScenarioPublication.objects.using(using).filter(scenario_id=scenario.pk).select_related(
            "previous_version", "selected_version",
        )[:10]
        context = {
            **self.admin_site.each_context(request),
            "title": "Scenario selection history" if active else f"Review scenario {action}",
            "opts": self.model._meta,
            "scenario": scenario,
            "active": active,
            "candidate_number": candidate_number,
            "base_number": base_number,
            "current_number": current_number,
            "selection_action": action,
            "change_url": change_url,
            "error_messages": error_messages,
            "diff": diff,
            "review_token": review_token,
            "history": history,
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request, "admin/reception/scenarioversion/selection_review.html", context, status=status,
        )

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
    retry_limit = forms.IntegerField(
        required=False, min_value=0, max_value=MAX_RETRY_LIMIT, label="Retry limit",
        help_text=(
            "Number of consecutive same-step retries before handover. Set to 0 for no limit or leave it blank "
            "to inherit. A positive limit requires a retry_exhausted transition to handover."
        ),
    )
    voice_silence_seconds = forms.IntegerField(
        required=False, min_value=1, max_value=MAX_SILENCE_SECONDS, label="Voice silence window (seconds)",
        help_text=(
            "Time to wait for speech at this step. Applies to each listening segment and to listening after the "
            "prompt ends. Leave blank to inherit."
        ),
    )
    voice_interruption = forms.ChoiceField(
        required=False, label="Voice interruption",
        choices=(("", "Use shared or built-in setting"), *VOICE_INTERRUPTION_CHOICES),
        help_text="Applies during browser voice. Skipping stops the spoken reply and starts listening.",
    )
    voice_timeout = forms.ChoiceField(
        required=False, label="Voice timeout action",
        choices=(("", "Use shared or built-in setting"), *VOICE_TIMEOUT_CHOICES),
        help_text="What happens if the guest stays silent through the final arrival listening window.",
    )
    parser_fallback = forms.ChoiceField(
        required=False, label="Parser fallback",
        choices=(("", "Use shared or built-in setting"), *PARSER_FALLBACK_CHOICES),
        help_text="Choose whether an unrecognized answer may be passed to the configured model provider.",
    )
    arrival_input = forms.ChoiceField(
        required=False, label="Expected arrival input",
        choices=(("", "Use shared or built-in setting"), *ARRIVAL_INPUT_CHOICES),
        help_text="Require both stay dates in one answer or allow guests to give their departure separately.",
    )
    chat_text = forms.CharField(
        required=False, max_length=MAX_CHAT_TEXT, label="Chat wording", widget=forms.Textarea(attrs={"rows": 4}),
    )
    voice_parts = VoicePartsField(
        required=False, label="Voice segments",
        help_text="Segments play from top to bottom. Clear a row's text, pause and Listen setting to remove it.",
    )
    clear_overrides = forms.BooleanField(required=False, label="Clear all step overrides")

    class Meta:
        model = ScenarioStep
        fields = ("version", "key", "position")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        step = self.instance
        scenario = step.version.scenario if step.pk else None
        applicable_fields = prompt_fields(scenario.language, scenario.channel, step.key) if scenario else ()
        settings = (
            ARRIVAL_INPUT_FIELD, RETRY_LIMIT_FIELD, SILENCE_WINDOW_FIELD, VOICE_INTERRUPTION_FIELD,
            VOICE_TIMEOUT_FIELD, PARSER_FALLBACK_FIELD,
        )
        for name in (CHAT_FIELD, VOICE_FIELD, *settings):
            if name not in applicable_fields:
                self.fields.pop(name, None)
        if not step.pk or step.configuration == {}:
            self.fields.pop("clear_overrides", None)
        if not scenario or not applicable_fields:
            return
        configuration = step.configuration if isinstance(step.configuration, dict) else {}
        for name in settings:
            if name in self.fields:
                self.initial[name] = configuration.get(name)
        default_source = "the shared default or built-in prompt" if step.version.base_version_id else "the built-in prompt"
        for name, default in answer_label_defaults(scenario.language, step.key).items():
            if label := self.fields.get(name):
                self.initial[name] = configuration.get(name, "")
                label.widget.attrs["placeholder"] = default
                label.help_text += f" Leave blank to use {default_source}."
        for key, fields in prompt_bindings(scenario.language, scenario.channel, step.key).items():
            chat_name, voice_name = fields.get(CHAT_FIELD), fields.get(VOICE_FIELD)
            if chat := self.fields.get(chat_name):
                default = default_chat_text(scenario.language, key)
                self.initial[chat_name] = configuration.get(chat_name, "")
                chat.help_text = f"Leave blank to use {default_source}." + _placeholder_help(_fields([default]))
                chat.widget.attrs["placeholder"] = default
            if voice := self.fields.get(voice_name):
                parts = configuration.get(voice_name)
                if isinstance(parts, list) and all(isinstance(part, dict) for part in parts):
                    self.initial[voice_name] = parts
                voice.widget.allow_listening = key in ARRIVAL_KEYS
                defaults = default_voice_parts(scenario.language, key)
                voice.help_text += f" Leave every row blank to use {default_source}."
                voice.help_text += _placeholder_help(_fields(part["text"] for part in defaults))
                if key == "option":
                    voice.widget.max_parts = 1
                    voice.help_text += " This segment is used inside the room list. Set its pause to zero."

    def clean(self):
        data = super().clean()
        step = self.instance
        if not step.pk:
            return data
        scenario = step.version.scenario
        editable = [name for name in prompt_fields(scenario.language, scenario.channel, step.key) if name in self.fields]
        if data.get("clear_overrides"):
            for name in editable:
                self.errors.pop(name, None)
            step.configuration = {}
        elif editable and all(name in data for name in editable):
            if not isinstance(step.configuration, dict):
                raise ValidationError("Clear the stored prompt overrides before entering new content.")
            configuration = {
                **{name: value for name, value in step.configuration.items() if name not in editable},
                **{
                    name: data[name] for name in editable
                    if data[name] or (name == RETRY_LIMIT_FIELD and data[name] == 0)
                },
            }
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
        scenario = obj.version.scenario
        prompts = prompt_fields(scenario.language, scenario.channel, obj.key)
        if not self.has_change_permission(request, obj):
            primary = tuple(name for name in prompts if name in (CHAT_FIELD, VOICE_FIELD))
            summary = ("outcome_wording",) if obj.key in OUTCOME_PROMPTS else ()
            labels = ("answer_button_labels",) if answer_label_fields(scenario.language, scenario.channel, obj.key) else ()
            parser = ("arrival_input_setting",) if ARRIVAL_INPUT_FIELD in prompts else ()
            fallback = ("parser_fallback_setting",) if PARSER_FALLBACK_FIELD in prompts else ()
            retry = ("retry_limit_setting",) if RETRY_LIMIT_FIELD in prompts else ()
            silence = ("voice_silence_setting",) if SILENCE_WINDOW_FIELD in prompts else ()
            interruption = ("voice_interruption_setting",) if VOICE_INTERRUPTION_FIELD in prompts else ()
            timeout = ("voice_timeout_setting",) if VOICE_TIMEOUT_FIELD in prompts else ()
            return (
                *self.fields, *primary, *summary, *labels, *parser, *fallback, *retry, *silence, *interruption, *timeout,
            )
        fields = (*self.fields, *prompts)
        if obj.configuration != {}:
            return (*fields, "clear_overrides")
        return fields

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None and self.has_change_permission(request, obj):
            variants = {}
            scenario = obj.version.scenario
            available = prompt_fields(scenario.language, scenario.channel, obj.key)
            for field, answer in answer_label_defaults(scenario.language, obj.key).items():
                if field not in available:
                    continue
                if field == ROOM_ANSWER_LABEL:
                    label = "Room answer button label"
                    help_text = "Keep {room} in the label. The button still selects the quoted room."
                else:
                    label = f"Answer button label for {answer}"
                    help_text = "Keep the same answer meaning. Only the displayed label changes."
                variants[field] = forms.CharField(
                    required=False, max_length=MAX_ANSWER_LABEL, label=label, help_text=help_text,
                )
            for prompt in OUTCOME_PROMPTS.get(obj.key, ()):
                label = prompt.replace("_", " ").capitalize()
                chat_name = outcome_field(prompt, CHAT_FIELD)
                if chat_name in available:
                    variants[chat_name] = forms.CharField(
                        required=False, max_length=MAX_CHAT_TEXT, label=f"{label} chat wording",
                        widget=forms.Textarea(attrs={"rows": 3}),
                    )
                voice_name = outcome_field(prompt, VOICE_FIELD)
                if voice_name in available:
                    variants[voice_name] = VoicePartsField(
                        required=False, label=f"{label} voice segments",
                        help_text="Segments play from top to bottom.",
                    )
            if variants:
                kwargs["form"] = type("ScenarioStepOutcomeForm", (self.form,), variants)
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        return ("version", "key") if obj is not None else ()

    @admin.display(description="Outcome wording")
    def outcome_wording(self, obj):
        from .scenario_publication import describe_outcome_wording

        return " | ".join(describe_outcome_wording(obj.configuration)) or "Default wording"

    @admin.display(description="Answer button labels")
    def answer_button_labels(self, obj):
        from .scenario_publication import describe_answer_labels

        return " | ".join(describe_answer_labels(obj.configuration)) or "Default labels"

    @admin.display(description="Expected arrival input")
    def arrival_input_setting(self, obj):
        from .scenario_publication import describe_arrival_input

        return describe_arrival_input(obj.configuration) or "Shared or built-in setting"

    @admin.display(description="Parser fallback")
    def parser_fallback_setting(self, obj):
        return PARSER_FALLBACK_LABELS.get(parser_fallback_mode(obj.configuration), "Shared or built-in setting")

    @admin.display(description="Retry limit")
    def retry_limit_setting(self, obj):
        from .scenario_publication import describe_retry_limit

        return describe_retry_limit(obj.configuration) or "Shared or built-in setting"

    @admin.display(description="Voice silence window (seconds)")
    def voice_silence_setting(self, obj):
        return silence_window_seconds(obj.configuration) or "Shared or built-in setting"

    @admin.display(description="Voice interruption")
    def voice_interruption_setting(self, obj):
        return VOICE_INTERRUPTION_LABELS.get(voice_interruption_mode(obj.configuration), "Shared or built-in setting")

    @admin.display(description="Voice timeout action")
    def voice_timeout_setting(self, obj):
        return VOICE_TIMEOUT_LABELS.get(voice_timeout_mode(obj.configuration), "Shared or built-in setting")

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
