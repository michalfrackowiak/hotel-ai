from django.contrib import admin
from django import forms
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
from django.views.decorators.http import require_GET

from .models import AgentConfiguration, SPEECH_LANGUAGES, SpeechModel, SpeechProvider, SpeechDeployment, SpeechSelection
from .preflight import check_speech_plan
from .controller_client import inventory_report


@admin.register(AgentConfiguration)
class AgentConfigurationAdmin(admin.ModelAdmin):
    list_display = ("provider", "model")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AgentConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class SpeechProviderForm(forms.ModelForm):
    languages = forms.MultipleChoiceField(choices=SPEECH_LANGUAGES, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = SpeechProvider
        fields = "__all__"

    def clean_languages(self):
        languages = self.cleaned_data["languages"]
        if self.instance.pk and self.instance.speech_models.exclude(language__in=languages).exists():
            raise forms.ValidationError("Remove models or voices for this language before removing provider support.")
        return languages


class SpeechModelFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        for form in self.forms:
            if form.cleaned_data and not form.cleaned_data.get("DELETE"):
                if form.cleaned_data.get("language") not in self.instance.languages:
                    raise forms.ValidationError("Every model or voice must use a selected provider language.")


class SpeechModelInline(admin.TabularInline):
    model = SpeechModel
    formset = SpeechModelFormSet
    extra = 0


@admin.register(SpeechProvider)
class SpeechProviderAdmin(admin.ModelAdmin):
    form = SpeechProviderForm
    inlines = (SpeechModelInline,)
    list_display = ("name", "task", "engine", "supported_languages", "architecture", "cpu_limit", "memory_mib")
    list_filter = ("task", "architecture")
    search_fields = ("name", "slug", "engine", "speech_models__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("adapter_version",)
    fieldsets = (
        ("Catalog only — saving does not change live speech services", {"fields": ("name", "slug", "task", "engine", "languages", "architecture")}),
        ("Deployment metadata", {"fields": ("image", "cpu_limit", "memory_mib", "adapter_version", "license", "notes")}),
    )

    @admin.display(description="Languages")
    def supported_languages(self, obj):
        return ", ".join(obj.languages)


@admin.register(SpeechDeployment)
class SpeechDeploymentAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "host", "container_name", "observed_state", "reference_count")
    readonly_fields = ("observed_state", "reference_count")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (("Planning only — does not change live containers or routing", {
        "fields": ("name", "slug", "provider", "host", "container_name", "observed_state", "reference_count", "notes"),
    }),)

    @admin.display(description="Planned assignment count")
    def reference_count(self, obj):
        return obj.planned_selections.count() if obj.pk else 0


@admin.register(SpeechSelection)
class SpeechSelectionAdmin(admin.ModelAdmin):
    change_list_template = "admin/model_providers/speechselection/change_list.html"
    list_display = ("language", "task", "deployment")
    readonly_fields = ("language", "task")
    fieldsets = (("Planned assignment only — does not activate a provider", {
        "fields": ("language", "task", "deployment"),
    }),)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def can_preflight(self, request):
        return request.user.has_perms([
            f"model_providers.view_{model}"
            for model in ("speechselection", "speechdeployment", "speechprovider", "speechmodel")
        ])

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {**(extra_context or {}), "can_preflight": self.can_preflight(request)})

    def get_urls(self):
        return [path("preflight/", self.admin_site.admin_view(require_GET(self.preflight_view)),
                     name="model_providers_speechselection_preflight"),
                path("inventory/", self.admin_site.admin_view(require_GET(self.inventory_view)),
                     name="model_providers_speechselection_inventory")] + super().get_urls()

    def inventory_view(self, request):
        if not self.can_preflight(request):
            raise PermissionDenied
        return TemplateResponse(request, "admin/model_providers/speechselection/inventory.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Observed speech container inventory", **inventory_report(),
        })

    def preflight_view(self, request):
        if not self.can_preflight(request):
            raise PermissionDenied
        return TemplateResponse(request, "admin/model_providers/speechselection/preflight.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Static speech plan preflight", **check_speech_plan(),
        })
