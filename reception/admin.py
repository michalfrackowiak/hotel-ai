from django import forms
from django.contrib import admin
from django.urls import path, reverse
from django.utils.html import format_html

from . import scenario_admin
from .models import HandoverMessage, ParserConfiguration, ParserSample, ScenarioPrompt, StaffHandover
from .parser_admin_preview import date_preview
from .scenario_editor import ScenarioPromptForm


class StaffHandoverForm(forms.ModelForm):
    reply = forms.CharField(required=False, max_length=500, widget=forms.Textarea)

    class Meta:
        model = StaffHandover
        fields = ("reply", "closed")


class HandoverTranscript(admin.TabularInline):
    model = HandoverMessage
    fields = ("created_at", "staff", "text")
    readonly_fields = fields
    ordering = ("id",)
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StaffHandover)
class StaffHandoverAdmin(admin.ModelAdmin):
    form = StaffHandoverForm
    fields = ("created_at", "language", "closed", "reply")
    readonly_fields = ("created_at", "language")
    list_display = ("id", "created_at", "language", "closed")
    list_filter = ("closed", "language")
    ordering = ("closed", "-created_at")
    inlines = (HandoverTranscript,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if reply := form.cleaned_data["reply"]:
            obj.messages.create(staff=True, text=reply)


@admin.register(ScenarioPrompt)
class ScenarioPromptAdmin(admin.ModelAdmin):
    form = ScenarioPromptForm
    fields = ("language", "key", "chat_text", "voice_parts", "reset_voice")
    readonly_fields = ("language", "key")
    list_display = ("key", "language", "custom_chat", "custom_voice")
    list_display_links = ("key",)
    list_filter = ("language",)
    search_fields = ("key",)
    ordering = ("language", "id")
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if obj and obj.key.startswith("phone_"):
            return [field for field in fields if field != "chat_text"]
        return fields

    @admin.display(boolean=True, description="Custom chat")
    def custom_chat(self, obj):
        return bool(obj.chat_text)

    @admin.display(boolean=True, description="Custom voice")
    def custom_voice(self, obj):
        return bool(obj.voice_parts)


class ParserSampleForm(forms.ModelForm):
    examples = {
        "check_in": "Enter one arrival date, a stay such as 2027-07-12 / 2027-07-15, or the client's full sentence with both dates.",
        "check_out": "Enter a departure date in a supported numeric or written format, number of nights, or stay length.",
        "guests": "Enter the total number of guests.",
        "room": "Enter an existing room code or an option number.",
        "email": "Enter the email address using the characters spoken or typed.",
        "phone": "Enter the same phone digits with the correct formatting.",
    }

    class Meta:
        model = ParserSample
        fields = ("corrected_result",)
        widgets = {"corrected_result": forms.TextInput(attrs={"size": 55})}
        labels = {"corrected_result": "Corrected result"}
        help_texts = {
            "corrected_result": "Matching future turns can use this correction if it is unambiguous and still valid. "
            "Clear the field to remove it.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("corrected_result")
        if field and self.instance.stage in self.examples:
            field.help_text = f"{self.examples[self.instance.stage]} {field.help_text}"

    def clean_corrected_result(self):
        from .parser_training import validate_correction

        text = self.cleaned_data["corrected_result"]
        if not text:
            return ""
        sample = self.instance
        return validate_correction(sample.stage, sample.text, text, language=sample.language, source=sample.source)


class ParserSampleReviewFilter(admin.SimpleListFilter):
    title = "review status"
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("pending", "No correction"), ("corrected", "Corrected")]

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(corrected_result="")
        if self.value() == "corrected":
            return queryset.exclude(corrected_result="")
        return queryset


@admin.register(ParserSample)
class ParserSampleAdmin(admin.ModelAdmin):
    form = ParserSampleForm
    fields = ("created_at", "language", "stage", "source", "text", "succeeded", "parsed_result", "corrected_result")
    readonly_fields = ("created_at", "language", "stage", "source", "text", "succeeded", "parsed_result")
    list_display = ("created_at", "language", "stage", "source", "input_excerpt", "parsed_result", "succeeded", "corrected")
    list_filter = ("language", "stage", "source", "succeeded", ParserSampleReviewFilter)
    ordering = ("-created_at", "-pk")
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).unexpired()

    @admin.display(description="Parser input")
    def input_excerpt(self, sample):
        return sample.text[:77] + "…" if len(sample.text) > 77 else sample.text

    @admin.display(boolean=True, description="Corrected")
    def corrected(self, sample):
        return bool(sample.corrected_result)


@admin.register(ParserConfiguration)
class ParserConfigurationAdmin(admin.ModelAdmin):
    readonly_fields = ("language",)
    list_display = (
        "language", "numeric_date_order", "allow_iso_dates", "allow_slash_dates", "allow_dot_dates",
        "allow_dash_dates", "allow_yearless_dates", "allow_two_digit_years", "preview_link",
    )
    list_display_links = ("language",)
    ordering = ("language",)
    fieldsets = (
        ("Language", {
            "fields": ("language",),
            "description": "These saved date settings apply to both chat and voice in this language.",
        }),
        ("Numeric date interpretation", {
            "fields": ("numeric_date_order",),
            "description": "Sets how ambiguous numeric dates such as 04/05/2027 are interpreted.",
        }),
        ("Accepted numeric formats", {
            "fields": (
                "allow_iso_dates", "allow_slash_dates", "allow_dot_dates", "allow_dash_dates",
                "allow_yearless_dates", "allow_two_digit_years",
            ),
            "description": "The ISO switch enables year-first dates such as 2027-04-05. "
            "Year-first dates with slashes or dots also require the corresponding separator switch. "
            "The hyphen switch controls dates that begin with a day or month. "
            "The yearless switch controls numeric dates such as 04/05. "
            "Written month names, relative dates and stay durations follow separate rules. "
            "Two-digit years use a 00–68 → 2000–2068, 69–99 → 1969–1999 cutoff.",
        }),
    )

    def get_urls(self):
        return [
            path(
                "<int:object_id>/preview-dates/",
                self.admin_site.admin_view(self.preview_dates),
                name="reception_parserconfiguration_preview_dates",
            ),
            *super().get_urls(),
        ]

    def preview_dates(self, request, object_id):
        return date_preview(request, self, object_id)

    @admin.display(description="Try saved settings")
    def preview_link(self, configuration):
        url = reverse("admin:reception_parserconfiguration_preview_dates", args=[configuration.pk])
        return format_html('<a href="{}">Preview {} dates</a>', url, configuration.get_language_display())

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
