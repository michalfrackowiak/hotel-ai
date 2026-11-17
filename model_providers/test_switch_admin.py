from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import SpeechDeployment, SpeechProvider, SpeechSelection, SpeechSwitchRequest
from .switch_admin import SALT
from .switch_client import SwitchTransportError
from .test_control_status import TOKEN, snapshot


@override_settings(HOTEL_SPEECH_CONTROL_SWITCH_ENABLED=True, HOTEL_SPEECH_CONTROL_HOST="fixture-host",
                   HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:8093", HOTEL_SPEECH_CONTROL_TOKEN=TOKEN)
class SwitchAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("switch-operator", is_staff=True)
        for codename in ("view_speechselection", "view_speechdeployment", "view_speechprovider", "view_speechmodel",
                         "switch_speech_controller"):
            self.user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.user)
        self.url = reverse("admin:model_providers_speechselection_switch_prepare")
        provider = SpeechProvider.objects.create(name="Planned STT", slug="planned-stt", task="stt", engine="test",
                                                languages=["en", "pl"], architecture="amd64")
        self.deployment = SpeechDeployment.objects.create(name="Planned worker", slug="planned-worker",
            provider=provider, host="fixture-host", container_name="new-stt-worker")
        self.selection, _ = SpeechSelection.objects.update_or_create(language="en", task="stt",
                                                                      defaults={"deployment": self.deployment})
        self.snapshot = snapshot()

    def review(self, client=None):
        with patch("model_providers.switch_admin.fetch_control_status", return_value=self.snapshot):
            return (client or self.client).get(self.url)

    def preview(self, client=None):
        return next(row["preview"] for row in self.review(client).context["rows"] if row["preview"])

    def prepare(self):
        response = self.client.post(self.url, {"preview": self.preview()})
        self.assertEqual(response.status_code, 302)
        return SpeechSwitchRequest.objects.get(), response.url

    def result(self, status="succeeded", reason="switched"):
        observed = deepcopy(self.snapshot["observed"])
        if status == "succeeded":
            observed["revision"] += 1
            observed["routes"][0][1] = "new-stt-worker"
        return dict(status=status, reason=reason, state=observed)

    def send(self, detail, **extra):
        return self.client.post(detail, {"action": "send", "confirmed": "on", **extra})

    def test_review_save_and_confirm_changes_only_the_reviewed_slot(self):
        with patch("model_providers.switch_admin.submit_switch") as submit:
            review = self.review()
            self.assertEqual(len(review.context["rows"]), 4)
            self.assertEqual([row["target"] for row in review.context["rows"] if row["preview"]], ["new-stt-worker"])
            self.assertFalse(SpeechSwitchRequest.objects.exists())
            saved, detail = self.prepare()
            self.assertEqual(saved.created_by, self.user)
            self.assertEqual(saved.previous_target, "stt-worker")
            self.assertEqual(saved.payload, dict(request_id="admin-" + saved.pk.hex, expected_revision=2,
                                                 slot=["en", "stt"], target="new-stt-worker"))
            self.assertIsNone(saved.attempted_at)
            self.client.get(detail)
            self.client.post(detail, {"action": "send"})
            submit.assert_not_called()

        def dispatch(payload):
            persisted = SpeechSwitchRequest.objects.get(pk=saved.pk)
            self.assertIsNotNone(persisted.attempted_at)
            self.assertIsNone(persisted.outcome)
            self.assertEqual(payload, saved.payload)
            return self.result()

        with patch("model_providers.switch_admin.submit_switch", side_effect=dispatch) as submit:
            self.assertContains(self.send(detail, target="posted-override"), "reported a successful switch")
            self.send(detail)
            submit.assert_called_once_with(saved.payload)
        saved.refresh_from_db()
        self.assertTrue(saved.closed)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.deployment_id, self.deployment.pk)

    def test_pending_or_disagreeing_controller_has_no_preview(self):
        status = self.snapshot
        for change in (dict(blocked=True, pending_requests=["fixture-other"]),
                       dict(observed=status["observed"] | {"revision": 3})):
            self.snapshot = status | change
            response = self.review()
            self.assertContains(response, "resolve pending recovery")
            self.assertNotIn("rows", response.context)

    def test_only_compatible_plan_from_configured_host_can_be_prepared(self):
        for changes in ({"host": "other"}, {"container_name": "stt-worker"}):
            with self.subTest(changes=changes), transaction.atomic():
                SpeechDeployment.objects.filter(pk=self.deployment.pk).update(**changes)
                self.assertFalse(any(row["preview"] for row in self.review().context["rows"]))
                transaction.set_rollback(True)
        SpeechProvider.objects.filter(pk=self.deployment.provider_id).update(languages=["pl"])
        self.assertFalse(any(row["preview"] for row in self.review().context["rows"]))

    def test_preview_rejects_tampering_expiry_user_and_plan_drift(self):
        preview = self.preview()
        decoded = signing.loads(preview, salt=SALT)
        for value in (preview + "tampered", signing.dumps(decoded | {"user": self.user.pk + 1}, salt=SALT),
                      signing.dumps(decoded | {"endpoint": "http://127.0.0.1:9000"}, salt=SALT)):
            self.assertContains(self.client.post(self.url, {"preview": value}), "plan or preview changed")
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 301):
            self.assertContains(self.client.post(self.url, {"preview": preview}), "plan or preview changed")
        self.selection.deployment = None
        self.selection.save(update_fields=["deployment"])
        self.assertContains(self.client.post(self.url, {"preview": preview}), "plan or preview changed")
        self.assertFalse(SpeechSwitchRequest.objects.exists())

    def test_duplicate_preparation_reuses_original_after_close_and_plan_edit(self):
        preview = self.preview()
        detail = self.client.post(self.url, {"preview": preview}).url
        self.client.post(detail, {"action": "cancel"})
        self.selection.deployment = None
        self.selection.save(update_fields=["deployment"])
        self.assertRedirects(self.client.post(self.url, {"preview": preview}), detail, fetch_redirect_response=False)
        self.assertEqual(SpeechSwitchRequest.objects.count(), 1)

    def test_open_request_prevents_competing_preparations_and_is_linked(self):
        previews = [self.preview(), self.preview()]
        detail = self.client.post(self.url, {"preview": previews[0]}).url
        self.assertRedirects(self.client.post(self.url, {"preview": previews[1]}), detail, fetch_redirect_response=False)
        current = SpeechSwitchRequest.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpeechSwitchRequest.objects.create(created_by=self.user, controller_url=current.controller_url,
                planned_host=current.planned_host, previous_target=current.previous_target, payload=current.payload)
        with patch("model_providers.switch_admin.fetch_control_status") as fetch:
            self.assertContains(self.client.get(self.url), detail)
            fetch.assert_not_called()

    def test_permissions_and_real_csrf_protect_both_post_routes(self):
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.user)
        body = {"preview": self.preview(secured)}
        with patch("model_providers.switch_admin.submit_switch") as submit:
            self.assertEqual(secured.post(self.url, body).status_code, 403)
            self.assertFalse(SpeechSwitchRequest.objects.exists())
            body["csrfmiddlewaretoken"] = secured.cookies["csrftoken"].value
            detail = secured.post(self.url, body).url
            self.assertEqual(secured.post(detail, {"action": "send", "confirmed": "on"}).status_code, 403)
            with override_settings(HOTEL_SPEECH_CONTROL_SWITCH_ENABLED=False):
                self.assertEqual(self.client.get(self.url).status_code, 403)
                self.assertEqual(self.send(detail).status_code, 403)
            for codename in ("switch_speech_controller", "view_speechmodel"):
                permission = Permission.objects.get(codename=codename)
                self.user.user_permissions.remove(permission)
                self.assertEqual(self.client.post(self.url, body).status_code, 403)
                self.assertEqual(self.send(detail).status_code, 403)
                self.user.user_permissions.add(permission)
            self.assertEqual(self.client.put(detail).status_code, 405)
            submit.assert_not_called()

    def test_confirm_validates_connection_and_cancel_only_applies_before_attempt(self):
        saved, detail = self.prepare()
        for setting in (dict(HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:9000"),
                        dict(HOTEL_SPEECH_CONTROL_HOST="other")):
            with override_settings(**setting), patch("model_providers.switch_admin.submit_switch") as submit:
                self.assertContains(self.send(detail), "controller configuration changed")
                submit.assert_not_called()
        saved.refresh_from_db()
        self.assertIsNone(saved.attempted_at)
        with patch("model_providers.switch_admin.submit_switch") as submit:
            self.assertContains(self.client.post(detail, {"action": "cancel"}), "cancelled before sending")
            self.send(detail)
            submit.assert_not_called()
        saved.refresh_from_db()
        self.assertTrue(saved.closed)
        self.assertEqual(saved.outcome, {"status": "cancelled"})

    def test_unconfirmed_outcome_retains_payload_and_reconciled_is_not_switch_success(self):
        saved, detail = self.prepare()
        with patch("model_providers.switch_admin.submit_switch", side_effect=[self.result("rejected", "busy"),
                SwitchTransportError(), self.result("reconciled", "operator_reconciled")]) as submit:
            self.send(detail)
            self.assertContains(self.send(detail), "unconfirmed")
            saved.refresh_from_db()
            self.assertIsNone(saved.outcome)
            self.assertFalse(saved.closed)
            self.assertContains(self.client.post(detail, {"action": "cancel"}), "only be cancelled before sending")
            response = self.send(detail)
            self.assertContains(response, "Controller recovery resolved this switch request")
            self.assertNotContains(response, "reported a successful switch")
            self.assertEqual([call.args[0] for call in submit.call_args_list], [saved.payload] * 3)
        saved.refresh_from_db()
        self.assertTrue(saved.closed)

    def test_older_transient_outcome_cannot_displace_newer_attempt_or_close(self):
        saved, detail = self.prepare()

        def overlap(outcome, *, complete=False):
            def dispatch(_payload):
                SpeechSwitchRequest.objects.filter(pk=saved.pk).update(attempted_at=timezone.now() + timedelta(seconds=1),
                    outcome=self.result() if complete else None, closed=complete)
                return outcome
            with patch("model_providers.switch_admin.submit_switch", side_effect=dispatch):
                self.send(detail)
            saved.refresh_from_db()

        overlap(self.result("rejected", "busy"))
        self.assertFalse(saved.closed)
        self.assertIsNone(saved.outcome)
        overlap(self.result(), complete=False)
        self.assertTrue(saved.closed)
        self.assertEqual(saved.outcome, self.result())
        SpeechSwitchRequest.objects.filter(pk=saved.pk).update(closed=False)
        overlap(self.result("rejected", "busy"), complete=True)
        self.assertEqual(saved.outcome, self.result())

    def test_pending_recovery_can_retry_but_definitive_outcome_closes(self):
        saved, detail = self.prepare()
        with patch("model_providers.switch_admin.submit_switch", side_effect=[
            self.result("recovery_required", "pending_reconciliation"),
            self.result("recovery_required", "rollback_incomplete"),
        ]):
            self.assertContains(self.send(detail), "Retry saved switch")
            saved.refresh_from_db()
            self.assertFalse(saved.closed)
            response = self.send(detail)
            self.assertContains(response, "Review live controller status and recovery")
            self.assertNotContains(response, "Retry saved switch")
        saved.refresh_from_db()
        self.assertTrue(saved.closed)
