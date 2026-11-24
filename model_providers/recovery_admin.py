"""Prepare immutable recovery records, then explicitly dispatch or retry them."""
import uuid

from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import timezone

from .control_status import ControlStatusError, configuration, fetch_control_status, state, validate_status
from .dispatch_generation import current_dispatch_generation
from .models import SpeechRecoveryRequest
from .recovery_client import RecoveryTransportError, submit_recovery


SALT = "speech-recovery-preview-v1"


class PrepareRecoveryForm(forms.Form):
    preview = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 3}))
    confirmed = forms.BooleanField(label="I reviewed the observed routes and want to prepare this recovery.")

    def clean_reason(self):
        value = self.cleaned_data["reason"]
        if any(ord(c) < 32 for c in value):
            raise forms.ValidationError("Use a single line without control characters.")
        return value


class ConfirmRecoveryForm(forms.Form):
    confirmed = forms.BooleanField(label="Send this saved recovery request to the controller.")


def can_recover(admin, request):
    return (settings.HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED is True and admin.can_preflight(request)
            and request.user.has_perm("model_providers.recover_speech_controller"))


def _context(admin, request):
    return {**admin.admin_site.each_context(request), "opts": admin.model._meta}


def _endpoint():
    return configuration()["url"].removesuffix("/v1/status")


def _rows(observed):
    return [dict(language="English" if language == "en" else "Polish", task=task.upper(), target=target)
            for (language, task), target in sorted(state(observed)[1].items())]


def prepare_view(admin, request):
    if not can_recover(admin, request):
        raise PermissionDenied
    context = _context(admin, request) | {"title": "Prepare speech controller recovery"}
    try:
        endpoint = _endpoint()
        generation = current_dispatch_generation()
        requests = SpeechRecoveryRequest.objects.filter(controller_url=endpoint, dispatch_generation=generation)
        existing = requests.filter(completed=False).first()
        if existing:
            return redirect("admin:model_providers_speechselection_recovery_detail", recovery_id=existing.pk)
        if request.method == "GET":
            snapshot = fetch_control_status()
            if not snapshot["blocked"] or snapshot["pending_recoveries"]:
                context["recovery_error"] = "Recovery cannot be prepared here: the controller must be blocked without an existing recovery request."
            else:
                preview = signing.dumps(dict(snapshot=snapshot, endpoint=endpoint, user=request.user.pk,
                                             request_id=uuid.uuid4().hex, generation=generation), salt=SALT)
                context.update(form=PrepareRecoveryForm(initial={"preview": preview}),
                               observed_rows=_rows(snapshot["observed"]), revision=snapshot["state"]["revision"],
                               observed_revision=snapshot["observed"]["revision"])
        else:
            form = PrepareRecoveryForm(request.POST)
            context["form"] = form
            valid = form.is_valid()
            try:
                preview = signing.loads(request.POST.get("preview", ""), salt=SALT, max_age=300)
                if (preview["endpoint"], preview["user"], preview.get("generation", "")) != (
                        endpoint, request.user.pk, generation):
                    raise ValueError
                request_id = uuid.UUID(hex=preview["request_id"])
                snapshot = validate_status(preview["snapshot"])
                if not snapshot["blocked"] or snapshot["pending_recoveries"]:
                    raise ValueError
                context.update(observed_rows=_rows(snapshot["observed"]), revision=snapshot["state"]["revision"],
                               observed_revision=snapshot["observed"]["revision"])
            except (signing.BadSignature, ValueError, TypeError, KeyError):
                form.add_error(None, "This review expired or is invalid. Refresh and review controller status again.")
            else:
                if valid:
                    payload = dict(recovery_id="admin-" + request_id.hex, expected_revision=snapshot["state"]["revision"],
                                   observed=snapshot["observed"], reason=form.cleaned_data["reason"])
                    try:
                        with transaction.atomic():
                            saved = SpeechRecoveryRequest.objects.create(id=request_id, created_by=request.user,
                                controller_url=endpoint, payload=payload, dispatch_generation=generation)
                    except IntegrityError:
                        saved = requests.filter(pk=request_id).first()
                        if saved is None:
                            saved = requests.filter(completed=False).first()
                        if saved is None:
                            raise
                    return redirect("admin:model_providers_speechselection_recovery_detail", recovery_id=saved.pk)
    except ControlStatusError as error:
        context["recovery_error"] = str(error)
    return TemplateResponse(request, "admin/model_providers/speechselection/recovery_prepare.html", context)


def detail_view(admin, request, recovery_id):
    if not can_recover(admin, request):
        raise PermissionDenied
    saved = get_object_or_404(SpeechRecoveryRequest, pk=recovery_id)
    context = _context(admin, request) | {"title": "Saved speech controller recovery", "saved": saved,
                                        "observed_rows": _rows(saved.payload["observed"])}
    form = ConfirmRecoveryForm(request.POST if request.method == "POST" else None)
    context["form"] = form
    try:
        generation_matches = saved.dispatch_generation == current_dispatch_generation()
        if not generation_matches:
            context["recovery_error"] = "This request belongs to another controller generation. It is retained for audit."
    except ControlStatusError as error:
        generation_matches = False
        context["recovery_error"] = str(error)
    context["dispatch_allowed"] = generation_matches
    if request.method == "POST" and form.is_valid() and not saved.completed and generation_matches:
        try:
            if _endpoint() != saved.controller_url:
                context["recovery_error"] = "The configured controller changed. Restore the original private connection before retrying this request."
            else:
                # Commit uncertainty before the network call; no transaction spans dispatch.
                pending = SpeechRecoveryRequest.objects.filter(pk=saved.pk, completed=False,
                                                                dispatch_generation=saved.dispatch_generation)
                attempted_at = timezone.now()
                if pending.update(attempted_at=attempted_at, outcome=None):
                    outcome = submit_recovery(saved.payload)
                    completed = outcome["status"] == "reconciled"
                    eligible = pending if completed else pending.filter(attempted_at=attempted_at)
                    eligible.update(outcome=outcome, completed=completed)
                saved.refresh_from_db()
        except (ControlStatusError, RecoveryTransportError) as error:
            context["recovery_error"] = str(error)
            saved.refresh_from_db()
    return TemplateResponse(request, "admin/model_providers/speechselection/recovery_detail.html", context)
