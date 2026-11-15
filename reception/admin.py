from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed
from django.urls import path, reverse
from django.utils.html import format_html

from .models import HandoverMessage, SpeechTrainingSample, StaffHandover
from .speech_training import MAX_EXPORT_SAMPLES, corrected_samples_zip, pcm_to_wav, training_text_allowed


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


class SpeechTrainingSampleForm(forms.ModelForm):
    class Meta:
        model = SpeechTrainingSample
        fields = ("corrected_transcript",)
        widgets = {"corrected_transcript": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "corrected_transcript": "Enter what the guest said. Delete clips that contain personal details or are unsuitable for training.",
        }

    def clean_corrected_transcript(self):
        text = self.cleaned_data["corrected_transcript"]
        if not training_text_allowed(text):
            raise forms.ValidationError("Use a single line of text without contact details.")
        return text


class SpeechTrainingStatusFilter(admin.SimpleListFilter):
    title = "review status"
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("pending", "Needs correction"), ("corrected", "Corrected")]

    def queryset(self, request, queryset):
        if self.value() == "pending":
            return queryset.filter(corrected_transcript="")
        if self.value() == "corrected":
            return queryset.exclude(corrected_transcript="")
        return queryset


@admin.register(SpeechTrainingSample)
class SpeechTrainingSampleAdmin(admin.ModelAdmin):
    form = SpeechTrainingSampleForm
    fields = ("created_at", "updated_at", "language", "stage", "audio_player", "transcript", "corrected_transcript")
    readonly_fields = ("created_at", "updated_at", "language", "stage", "audio_player", "transcript")
    list_display = ("id", "created_at", "language", "stage", "reviewed")
    list_filter = (SpeechTrainingStatusFilter, "language", "stage")
    ordering = ("-created_at",)
    actions = ["export_corrected"]

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).unexpired().defer("pcm")

    def get_urls(self):
        return [
            path(
                "<int:object_id>/audio/",
                self.admin_site.admin_view(self.audio),
                name="reception_speechtrainingsample_audio",
            ),
            *super().get_urls(),
        ]

    @admin.display(boolean=True, description="Corrected")
    def reviewed(self, sample):
        return bool(sample.corrected_transcript)

    @admin.display(description="Recording")
    def audio_player(self, sample):
        url = reverse("admin:reception_speechtrainingsample_audio", args=(sample.pk,))
        return format_html('<audio controls preload="none" src="{}"></audio>', url)

    def audio(self, request, object_id):
        if request.method not in ("GET", "HEAD"):
            return HttpResponseNotAllowed(("GET", "HEAD"))
        if not self.has_view_permission(request):
            raise PermissionDenied
        sample = self.get_object(request, str(object_id))
        if sample is None:
            raise Http404
        if not self.has_view_permission(request, sample):
            raise PermissionDenied
        return HttpResponse(
            pcm_to_wav(sample.pcm), content_type="audio/wav", headers={"X-Content-Type-Options": "nosniff"},
        )

    @admin.action(permissions=["change"], description="Export selected corrected samples as WAV and transcript pairs")
    def export_corrected(self, request, queryset):
        rows = queryset.exclude(corrected_transcript="")
        count = rows.count()
        if not 1 <= count <= MAX_EXPORT_SAMPLES:
            self.message_user(request, f"Select between 1 and {MAX_EXPORT_SAMPLES} corrected samples.", messages.WARNING)
            return None
        rows = rows.order_by("pk").values_list("pcm", "corrected_transcript", "language", "stage")
        archive = corrected_samples_zip(rows[:MAX_EXPORT_SAMPLES].iterator(chunk_size=1))
        return FileResponse(archive, as_attachment=True, filename="speech-training-samples.zip", content_type="application/zip")
