from dataclasses import dataclass
from datetime import timedelta

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .parser_training import apply_reviewed_correction, rule_key, validate_correction
from .parsing import StayDates, parse_stay_input


DATE_STAGES = (("check_in", "Arrival or complete stay"), ("check_out", "Departure or stay length"))


class DatePreviewForm(forms.Form):
    stage = forms.ChoiceField(
        label="Booking question",
        choices=DATE_STAGES,
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


@dataclass(frozen=True)
class ReplayCase:
    key: str
    source: str
    stage: str
    reply: str
    expected_days: int | None
    arrival_days: int | None = None
    correctable: bool = False


REPLAY_CASES = {
    "en": (
        ReplayCase("voice-arrival", "voice", "check_in", "we can arrive the day after to marrow", 2, correctable=True),
        ReplayCase("chat-arrival", "chat", "check_in", "we can arrive the day after to marrow", None),
        ReplayCase("direct-arrival", "chat", "check_in", "tomorrow", 1),
        ReplayCase("checkout", "voice", "check_out", "for three nights", 4, 1),
        ReplayCase("voice-departure", "voice", "check_out", "for tree knights", 4, 1, correctable=True),
    ),
    "pl": (
        ReplayCase("voice-arrival", "voice", "check_in", "przyjazd po jutrze", 2, correctable=True),
        ReplayCase("chat-arrival", "chat", "check_in", "przyjazd po jutrze", None),
        ReplayCase("direct-arrival", "chat", "check_in", "jutro", 1),
        ReplayCase("checkout", "voice", "check_out", "na trzy noce", 4, 1),
        ReplayCase("voice-departure", "voice", "check_out", "na czy nocę", 4, 1, correctable=True),
    ),
}


class CorrectionReplayForm(forms.Form):
    example = forms.ChoiceField(label="Fictional example")
    correction = forms.CharField(
        label="Proposed correction", max_length=500,
        widget=forms.TextInput(attrs={"autocomplete": "off", "size": 55}),
    )

    def __init__(self, *args, language, **kwargs):
        super().__init__(*args, **kwargs)
        self.language = language
        self.cases = {case.key: case for case in REPLAY_CASES[language]}
        self.fields["example"].choices = [
            (case.key, f"{dict(DATE_STAGES)[case.stage]} · {case.reply}")
            for case in self.cases.values() if case.correctable
        ]

    def clean(self):
        data = super().clean()
        if data.get("example") and data.get("correction"):
            case = self.cases[data["example"]]
            try:
                data["correction"] = validate_correction(
                    case.stage, case.reply, data["correction"], language=self.language, source=case.source,
                )
            except ValidationError as error:
                self.add_error("correction", error)
        return data

    def replay(self, *, today):
        selected = self.cases[self.cleaned_data["example"]]
        scope = self._scope(selected)
        rows = []
        for case in self.cases.values():
            arrival = today + timedelta(days=case.arrival_days) if case.arrival_days is not None else None
            expected = today + timedelta(days=case.expected_days) if case.expected_days is not None else None
            baseline = parse_stay_input(case.stage, case.reply, self.language, check_in=arrival, today=today)
            effective = baseline
            if self._scope(case) == scope:
                state = {"stage": case.stage, "language": self.language}
                if arrival is not None:
                    state["check_in"] = arrival.isoformat()
                candidate = apply_reviewed_correction(
                    state, case.reply, baseline, self.cleaned_data["correction"], source=case.source,
                )
                if candidate is not None:
                    effective = candidate
            rows.append({
                "case": case, "question": dict(DATE_STAGES)[case.stage], "arrival": arrival,
                "expected": expected, "baseline": baseline, "effective": effective,
                "baseline_matches": baseline == expected, "effective_matches": effective == expected,
            })
        return rows

    def _scope(self, case):
        return case.source, rule_key(self.language, case.stage, case.reply)


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
    submitted = request.POST if request.method == "POST" else None
    replay_submitted = submitted is not None and submitted.get("action") == "replay"
    form = DatePreviewForm(
        submitted if not replay_submitted else None,
        initial={"reply": examples[configuration.language]},
    )
    today = timezone.localdate()
    result = form.preview(configuration.language, today=today) if form.is_bound and form.is_valid() else None
    replay_form = CorrectionReplayForm(submitted if replay_submitted else None, language=configuration.language)
    replay = replay_form.replay(today=today) if replay_form.is_bound and replay_form.is_valid() else None
    return TemplateResponse(request, "admin/reception/parserconfiguration/date_preview.html", {
        **model_admin.admin_site.each_context(request),
        "opts": model_admin.model._meta,
        "title": f"Preview dates · {configuration.get_language_display()}",
        "configuration": configuration,
        "today": today,
        "form": form,
        "result": result,
        "replay_form": replay_form,
        "replay": replay,
    })
