from django import forms
from django.contrib import admin

from .models import HandoverMessage, StaffHandover


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
