from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .parsing import StayDates, parse_stay_input


class DatePreviewForm(forms.Form):
    stage = forms.ChoiceField(
        label="Booking question",
        choices=(("check_in", "Arrival or complete stay"), ("check_out", "Departure or stay length")),
    )
    reply = forms.CharField(
        label="Fictional guest reply",
        max_length=500,
        widget=forms.TextInput(attrs={"autocomplete": "off", "size": 55}),
    )
    check_in = forms.DateField(
        label="Known arrival date",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
        help_text="Required for a departure question. Stay lengths are counted from this date.",
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("stage") == "check_out" and "check_in" in cleaned and cleaned["check_in"] is None:
            self.add_error("check_in", "Enter the known arrival date for this example.")
        return cleaned

    def preview(self, language, *, today):
        stage = self.cleaned_data["stage"]
        result = parse_stay_input(
            stage, self.cleaned_data["reply"], language, check_in=self.cleaned_data["check_in"], today=today,
        )
        if isinstance(result, StayDates):
            return {"arrival": result.check_in, "departure": result.check_out}
        key = "arrival" if stage == "check_in" else "departure"
        return {key: result} if result is not None else {}


@require_http_methods(["GET", "HEAD", "POST"])
def date_preview(request, model_admin, object_id):
    if not model_admin.has_view_or_change_permission(request):
        raise PermissionDenied
    configuration = model_admin.get_object(request, object_id)
    if configuration is None:
        raise Http404
    if not model_admin.has_view_or_change_permission(request, configuration):
        raise PermissionDenied
    examples = {
        "en": "from tomorrow for three days",
        "pl": "od jutra na trzy dni",
    }
    form = DatePreviewForm(
        request.POST if request.method == "POST" else None,
        initial={"reply": examples[configuration.language]},
    )
    today = timezone.localdate()
    result = form.preview(configuration.language, today=today) if form.is_bound and form.is_valid() else None
    return TemplateResponse(request, "admin/reception/parserconfiguration/date_preview.html", {
        **model_admin.admin_site.each_context(request),
        "opts": model_admin.model._meta,
        "title": f"Preview dates · {configuration.get_language_display()}",
        "configuration": configuration,
        "today": today,
        "form": form,
        "result": result,
    })
