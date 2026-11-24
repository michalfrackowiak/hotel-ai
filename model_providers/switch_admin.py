"""Review a planned assignment, save it, and dispatch the exact saved switch."""
import re
import uuid

from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import timezone

from .control_status import ControlStatusError, SLOTS, configuration, fetch_control_status, identifier, state
from .dispatch_generation import current_dispatch_generation
from .models import SPEECH_LANGUAGES, SpeechSelection, SpeechSwitchRequest
from .switch_client import SwitchTransportError, submit_switch


SALT = "speech-switch-preview-v1"
DETAIL_URL = "admin:model_providers_speechselection_switch_detail"
RECOVERY_RESULTS = {"rollback_incomplete", "resume_failed_after_checkpoint"}
JOURNALED_REJECTIONS = {"stale_revision", "unapproved_or_incompatible_target", "admission_denied", "admission_unavailable"}


class ConfirmSwitchForm(forms.Form):
    confirmed = forms.BooleanField(label="I want to send this saved switch to the controller.")


def can_switch(admin, request):
    return (settings.HOTEL_SPEECH_CONTROL_SWITCH_ENABLED is True and admin.can_preflight(request)
            and request.user.has_perm("model_providers.switch_speech_controller"))


def _connection():
    endpoint = configuration()["url"].removesuffix("/v1/status")
    host = settings.HOTEL_SPEECH_CONTROL_HOST
    if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", host):
        raise ControlStatusError("configuration")
    return endpoint, host


def _planned_assignments(host):
    planned = SpeechSelection.objects.filter(deployment__host=host).select_related("deployment__provider")
    return {(item.language, item.task): item.deployment for item in planned
            if (item.language, item.task) in SLOTS and identifier(item.deployment.container_name)
            and item.deployment.provider.task == item.task
            and isinstance(item.deployment.provider.languages, list)
            and item.language in item.deployment.provider.languages}


def _preview_rows(request, endpoint, host, generation, snapshot):
    revision, routes = state(snapshot["state"])
    observed = state(snapshot["observed"])
    if snapshot["blocked"] or (revision, routes) != observed:
        return {"switch_error": "Review the live controller status and resolve pending recovery before preparing a switch."}

    planned = _planned_assignments(host)
    rows = []
    for slot in sorted(SLOTS):
        deployment = planned.get(slot)
        current = routes[slot]
        target = deployment.container_name if deployment else None
        preview = None
        if target and target != current:
            preview = signing.dumps(dict(endpoint=endpoint, host=host, user=request.user.pk, generation=generation,
                request_id=uuid.uuid4().hex, slot=list(slot), revision=revision, current=current, target=target,
                deployment=deployment.pk, provider=deployment.provider_id), salt=SALT)
        rows.append(dict(language=dict(SPEECH_LANGUAGES)[slot[0]], task=slot[1].upper(),
                         current=current, target=target, preview=preview))
    return {"rows": rows, "revision": revision, "host": host}


def _save_switch(request, endpoint, host, generation, requests):
    preview = signing.loads(request.POST.get("preview", ""), salt=SALT, max_age=300)
    if (preview["endpoint"], preview["host"], preview["user"], preview.get("generation", "")) != (
            endpoint, host, request.user.pk, generation):
        raise ValueError
    request_id = uuid.UUID(hex=preview["request_id"])
    existing = requests.filter(pk=request_id).first()
    if existing:
        return existing
    slot = tuple(preview["slot"])
    deployment = _planned_assignments(host).get(slot)
    if not deployment or (deployment.pk, deployment.provider_id, deployment.container_name) != (
            preview["deployment"], preview["provider"], preview["target"]):
        raise ValueError

    payload = dict(request_id="admin-" + request_id.hex, expected_revision=preview["revision"],
                   slot=list(slot), target=preview["target"])
    try:
        with transaction.atomic():
            return SpeechSwitchRequest.objects.create(id=request_id, created_by=request.user, controller_url=endpoint,
                planned_host=host, previous_target=preview["current"], payload=payload, dispatch_generation=generation)
    except IntegrityError:
        saved = requests.filter(pk=request_id).first()
        if saved is None:
            saved = requests.filter(closed=False).first()
        if saved is None:
            raise
        return saved


def prepare_view(admin, request):
    if not can_switch(admin, request):
        raise PermissionDenied
    context = {**admin.admin_site.each_context(request), "opts": admin.model._meta,
               "title": "Review planned provider switches",
               "history": SpeechSwitchRequest.objects.select_related("created_by")[:20]}
    try:
        endpoint, host = _connection()
        generation = current_dispatch_generation()
        requests = SpeechSwitchRequest.objects.filter(controller_url=endpoint, dispatch_generation=generation)
        if request.method == "POST":
            saved = _save_switch(request, endpoint, host, generation, requests)
            return redirect(DETAIL_URL, switch_id=saved.pk)
        pending = requests.filter(closed=False).first()
        if pending:
            context["pending"] = pending
        else:
            context.update(_preview_rows(request, endpoint, host, generation, fetch_control_status()))
    except ControlStatusError as error:
        context["switch_error"] = str(error)
    except (signing.BadSignature, TypeError, KeyError, ValueError):
        context["switch_error"] = "The plan or preview changed. Refresh and review the live routes again."
    return TemplateResponse(request, "admin/model_providers/speechselection/switch_prepare.html", context)


def _is_final(outcome):
    status, reason = outcome["status"], outcome["reason"]
    if status == "recovery_required":
        return reason in RECOVERY_RESULTS
    return status != "rejected" or reason != "busy"


def _recorded(outcome):
    status, reason = outcome["status"], outcome["reason"]
    if status == "rejected":
        return reason in JOURNALED_REJECTIONS
    return _is_final(outcome)


def detail_view(admin, request, switch_id):
    if not can_switch(admin, request):
        raise PermissionDenied
    saved = get_object_or_404(SpeechSwitchRequest, pk=switch_id)
    form = ConfirmSwitchForm(request.POST if request.method == "POST" else None)
    context = {**admin.admin_site.each_context(request), "opts": admin.model._meta,
               "title": "Saved speech provider switch", "saved": saved, "form": form,
               "language": dict(SPEECH_LANGUAGES)[saved.payload["slot"][0]],
               "task": saved.payload["slot"][1].upper()}
    try:
        generation_matches = saved.dispatch_generation == current_dispatch_generation()
        if not generation_matches:
            context["switch_error"] = "This request belongs to another controller generation. It is retained for audit."
    except ControlStatusError as error:
        generation_matches = False
        context["switch_error"] = str(error)
    context["dispatch_allowed"] = generation_matches
    if request.method == "POST" and not saved.closed and generation_matches:
        pending = SpeechSwitchRequest.objects.filter(pk=saved.pk, closed=False,
                                                      dispatch_generation=saved.dispatch_generation)
        action = request.POST.get("action")
        if action == "cancel":
            if not pending.filter(attempted_at__isnull=True).update(closed=True, outcome={"status": "cancelled"}):
                context["switch_error"] = "This request can only be cancelled before sending it."
        elif action == "send" and form.is_valid():
            try:
                if _connection() != (saved.controller_url, saved.planned_host):
                    context["switch_error"] = "The controller configuration changed. Restore it before sending this request."
                else:
                    # Save the attempt before making a network call outside any transaction.
                    attempted_at = timezone.now()
                    if pending.update(attempted_at=attempted_at, outcome=None):
                        outcome = submit_switch(saved.payload)
                        eligible = pending if _recorded(outcome) else pending.filter(attempted_at=attempted_at)
                        eligible.update(outcome=outcome, closed=_is_final(outcome))
            except (ControlStatusError, SwitchTransportError) as error:
                context["switch_error"] = str(error)
        saved.refresh_from_db()
    return TemplateResponse(request, "admin/model_providers/speechselection/switch_detail.html", context)
