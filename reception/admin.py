from datetime import timedelta

from django import forms
from django.contrib import admin
from django.db.models import OuterRef, Subquery
from django.urls import path, reverse
from django.utils import timezone
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


class FirstStaffResponseFilter(admin.SimpleListFilter):
    title = "first staff response"
    parameter_name = "first_response"

    def lookups(self, request, model_admin):
        return (
            ("awaiting", "Awaiting reply"),
            ("replied", "Replied"),
            ("closed_without_reply", "Closed without reply"),
        )

    def queryset(self, request, queryset):
        status = self.value()
        if status == "awaiting":
            return queryset.filter(closed=False, first_staff_reply_at__isnull=True)
        if status == "replied":
            return queryset.filter(first_staff_reply_at__isnull=False)
        if status == "closed_without_reply":
            return queryset.filter(closed=True, first_staff_reply_at__isnull=True)
        return queryset


@admin.register(StaffHandover)
class StaffHandoverAdmin(admin.ModelAdmin):
    form = StaffHandoverForm
    fields = ("created_at", "language", "first_staff_response", "closed", "reply")
    readonly_fields = ("created_at", "language", "first_staff_response")
    list_display = ("id", "created_at", "language", "closed", "first_staff_response")
    list_filter = (FirstStaffResponseFilter, "closed", "language")
    ordering = ("closed", "-created_at")
    inlines = (HandoverTranscript,)

    def get_queryset(self, request):
        first_reply = HandoverMessage.objects.filter(
            handover_id=OuterRef("pk"), staff=True,
        ).order_by("created_at").values("created_at")[:1]
        return super().get_queryset(request).annotate(first_staff_reply_at=Subquery(first_reply))

    @admin.display(description="First staff response")
    def first_staff_response(self, obj):
        if obj.first_staff_reply_at is None:
            return "Closed without reply" if obj.closed else "Awaiting reply"
        elapsed = obj.first_staff_reply_at - obj.created_at
        return str(timedelta(days=elapsed.days, seconds=elapsed.seconds))

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
        fields = ("corrected_result", "correction_enabled")
        widgets = {"corrected_result": forms.TextInput(attrs={"size": 55})}
        labels = {"corrected_result": "Corrected result", "correction_enabled": "Enable correction"}
        help_texts = {
            "corrected_result": "Matching future turns can use this correction if it is unambiguous and still valid. "
            "Clear the field to remove it.",
            "correction_enabled": "Turn off to retain the correction without applying it. An eligible correction has no conflicting reviewed value and must still pass booking checks.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("corrected_result")
        if field and self.instance.stage in self.examples:
            field.help_text = f"{self.examples[self.instance.stage]} {field.help_text}"

    def clean(self):
        from .parser_training import validate_correction

        data = super().clean()
        text = data.get("corrected_result")
        sample = self.instance
        if text and (text != sample.corrected_result or data.get("correction_enabled")):
            try:
                data["corrected_result"] = validate_correction(
                    sample.stage, sample.text, text, language=sample.language, source=sample.source,
                )
            except forms.ValidationError as error:
                self.add_error("corrected_result", error)
        return data


class ParserSampleReviewFilter(admin.SimpleListFilter):
    title = "review status"
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("pending", "No correction"), ("corrected", "Corrected"),
                ("enabled", "Enabled correction"), ("disabled", "Disabled correction")]

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(corrected_result="")
        if self.value() == "corrected":
            return queryset.exclude(corrected_result="")
        if self.value() in ("enabled", "disabled"):
            return queryset.exclude(corrected_result="").filter(correction_enabled=self.value() == "enabled")
        return queryset


@admin.register(ParserSample)
class ParserSampleAdmin(admin.ModelAdmin):
    form = ParserSampleForm
    fields = ("created_at", "language", "stage", "source", "text", "succeeded", "parsed_result", "corrected_result",
              "correction_enabled", "correction_status")
    readonly_fields = ("created_at", "language", "stage", "source", "text", "succeeded", "parsed_result",
                       "correction_status")
    list_display = ("created_at", "language", "stage", "source", "input_excerpt", "parsed_result", "succeeded",
                    "correction_status")
    list_filter = ("language", "stage", "source", "succeeded", ParserSampleReviewFilter)
    ordering = ("-created_at", "-pk")
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        now = timezone.now()
        return super().get_queryset(request).unexpired(now).with_correction_conflicts(now)

    @admin.display(description="Parser input")
    def input_excerpt(self, sample):
        return sample.text[:77] + "…" if len(sample.text) > 77 else sample.text

    @admin.display(description="Correction status")
    def correction_status(self, sample):
        if not sample.corrected_result:
            return "No correction"
        if not sample.correction_enabled:
            return "Disabled"
        if sample.correction_conflicts:
            return "Conflicting"
        return "Eligible"


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
