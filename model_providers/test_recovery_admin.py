from copy import deepcopy
from datetime import timedelta
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import signing
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import SpeechRecoveryRequest
from .recovery_admin import SALT
from .recovery_client import RecoveryTransportError
from .test_control_status import TOKEN, snapshot


@override_settings(HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=True,
                   HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:8093", HOTEL_SPEECH_CONTROL_TOKEN=TOKEN)
class RecoveryAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("recovery-operator", is_staff=True)
        for name in ("view_speechselection", "view_speechdeployment", "view_speechprovider", "view_speechmodel",
                     "recover_speech_controller"):
            self.user.user_permissions.add(Permission.objects.get(codename=name))
        self.client.force_login(self.user)
        self.url = reverse("admin:model_providers_speechselection_recovery_prepare")
        self.snapshot = snapshot()
        self.snapshot["blocked"] = True
        self.snapshot["pending_requests"] = ["interrupted-switch"]

    def preview(self, client=None):
        with patch("model_providers.recovery_admin.fetch_control_status", return_value=self.snapshot):
            return (client or self.client).get(self.url)

    def prepare(self):
        preview = self.preview().context["form"].initial["preview"]
        response = self.client.post(self.url, {"preview": preview, "reason": "Verified workers", "confirmed": "on"})
        self.assertEqual(response.status_code, 302)
        saved = SpeechRecoveryRequest.objects.get()
        return saved, response.url

    def result(self, saved, status="reconciled", reason="operator_reconciled"):
        return dict(status=status, reason=reason, state=deepcopy(saved.payload["observed"]))

    def test_review_and_prepare_do_not_dispatch_and_confirmation_is_durable(self):
        with patch("model_providers.recovery_admin.submit_recovery") as send:
            response = self.preview()
            self.assertContains(response, "English", count=2)
            self.assertEqual(SpeechRecoveryRequest.objects.count(), 0)
            saved, detail = self.prepare()
            self.assertEqual(saved.created_by, self.user)
            self.assertIsNone(saved.attempted_at)
            self.client.get(detail)
            self.client.post(detail, {})
            send.assert_not_called()
        def dispatch(payload):
            persisted = SpeechRecoveryRequest.objects.get(pk=saved.pk)
            self.assertEqual(payload, persisted.payload)
            self.assertIsNotNone(persisted.attempted_at)
            self.assertIsNone(persisted.outcome)
            return self.result(saved)
        with patch("model_providers.recovery_admin.submit_recovery", side_effect=dispatch) as send:
            self.assertContains(self.client.post(detail, {"confirmed": "on"}), "Recovery confirmed")
            self.client.post(detail, {"confirmed": "on"})
            self.assertEqual(send.call_count, 1)
        saved.refresh_from_db()
        self.assertTrue(saved.completed)

    def test_timeout_retry_keeps_original_payload_and_clears_old_rejection(self):
        saved, detail = self.prepare()
        rejected = self.result(saved, "rejected", "reconciliation_unverified")
        with patch("model_providers.recovery_admin.submit_recovery", return_value=rejected):
            self.client.post(detail, {"confirmed": "on"})
        with patch("model_providers.recovery_admin.submit_recovery", side_effect=RecoveryTransportError()) as send:
            self.assertContains(self.client.post(detail, {"confirmed": "on"}), "unconfirmed")
            self.assertEqual(send.call_args.args[0], saved.payload)
        saved.refresh_from_db()
        self.assertFalse(saved.completed)
        self.assertIsNone(saved.outcome)
        with patch("model_providers.recovery_admin.submit_recovery", return_value=self.result(saved)) as send:
            self.client.post(detail, {"confirmed": "on"})
            self.assertEqual(send.call_args.args[0], saved.payload)

    def test_signed_preview_binds_user_endpoint_and_expiry(self):
        preview = self.preview().context["form"].initial["preview"]
        decoded = signing.loads(preview, salt=SALT)
        for value in (preview + "tampered", signing.dumps(decoded | {"user": self.user.pk + 1}, salt=SALT),
                      signing.dumps(decoded | {"endpoint": "http://127.0.0.1:9999"}, salt=SALT)):
            self.assertContains(self.client.post(self.url, {"preview": value, "reason": "Verified", "confirmed": "on"}),
                                "expired or is invalid")
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 301):
            self.assertContains(self.client.post(self.url, {"preview": preview, "reason": "Verified", "confirmed": "on"}),
                                "expired or is invalid")
        self.assertFalse(SpeechRecoveryRequest.objects.exists())

    def test_permission_flag_and_methods_deny_before_dispatch(self):
        with patch("model_providers.recovery_admin.fetch_control_status") as fetch, \
                patch("model_providers.recovery_admin.submit_recovery") as send:
            self.client.logout()
            self.assertEqual(self.client.get(self.url).status_code, 302)
            self.client.force_login(self.user)
            with override_settings(HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=False):
                self.assertEqual(self.client.get(self.url).status_code, 403)
            for codename in ("recover_speech_controller", "view_speechmodel"):
                permission = Permission.objects.get(codename=codename)
                self.user.user_permissions.remove(permission)
                self.assertEqual(self.client.post(self.url, {}).status_code, 403)
                self.user.user_permissions.add(permission)
            self.assertEqual(self.client.put(self.url).status_code, 405)
            fetch.assert_not_called()
            send.assert_not_called()

    def test_csrf_is_required_for_prepare_and_dispatch(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = self.preview(client)
        preview = response.context["form"].initial["preview"]
        body = {"preview": preview, "reason": "Verified", "confirmed": "on"}
        with patch("model_providers.recovery_admin.submit_recovery", return_value=None) as send:
            self.assertEqual(client.post(self.url, body).status_code, 403)
            self.assertFalse(SpeechRecoveryRequest.objects.exists())
            body["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
            response = client.post(self.url, body)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(client.post(response.url, {"confirmed": "on"}).status_code, 403)
            send.assert_not_called()

    def test_unblocked_or_foreign_pending_recovery_cannot_prepare(self):
        for change in ({"blocked": False, "pending_requests": []}, {"pending_recoveries": ["external"]}):
            with patch("model_providers.recovery_admin.fetch_control_status", return_value=self.snapshot | change):
                response = self.client.get(self.url)
                self.assertContains(response, "Recovery cannot be prepared here")
                self.assertNotIn("form", response.context)
        self.assertFalse(SpeechRecoveryRequest.objects.exists())

    def test_invalid_reason_preserves_review_rows(self):
        preview = self.preview().context["form"].initial["preview"]
        response = self.client.post(self.url, {"preview": preview, "reason": "bad\nreason", "confirmed": "on"})
        self.assertContains(response, "single line")
        self.assertContains(response, "English", count=2)
        self.assertContains(response, "stt-worker", count=2)
        self.assertFalse(SpeechRecoveryRequest.objects.exists())

    def test_controller_change_blocks_existing_payload_and_get_is_read_only(self):
        saved, detail = self.prepare()
        with override_settings(HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:9999"), \
                patch("model_providers.recovery_admin.submit_recovery") as send:
            self.client.get(detail)
            self.assertContains(self.client.post(detail, {"confirmed": "on"}), "configured controller changed")
            send.assert_not_called()
        saved.refresh_from_db()
        self.assertIsNone(saved.attempted_at)
        self.assertIsNone(saved.outcome)

    def test_one_unresolved_request_per_endpoint_is_enforced_in_database(self):
        saved, detail = self.prepare()
        self.assertRedirects(self.client.get(self.url), detail, fetch_redirect_response=False)
        self.assertRedirects(self.client.post(self.url, {}), detail, fetch_redirect_response=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpeechRecoveryRequest.objects.create(created_by=self.user, controller_url=saved.controller_url,
                                                payload=saved.payload)
        self.assertEqual(SpeechRecoveryRequest.objects.count(), 1)

    def test_repeated_preparation_redirects_to_original_completed_request(self):
        preview = self.preview().context["form"].initial["preview"]
        body = {"preview": preview, "reason": "Verified", "confirmed": "on"}
        detail = self.client.post(self.url, body).url
        saved = SpeechRecoveryRequest.objects.get()
        with patch("model_providers.recovery_admin.submit_recovery", return_value=self.result(saved)):
            self.client.post(detail, {"confirmed": "on"})
        self.assertRedirects(self.client.post(self.url, body | {"reason": "Edited"}), detail, fetch_redirect_response=False)
        saved.refresh_from_db()
        self.assertTrue(saved.completed)
        self.assertEqual(saved.payload["reason"], "Verified")
        self.assertEqual(SpeechRecoveryRequest.objects.count(), 1)

    def test_older_rejection_cannot_overwrite_new_attempt_or_completion(self):
        saved, detail = self.prepare()
        rejected = self.result(saved, "rejected", "reconciliation_unverified")
        def newer_attempt(_payload):
            SpeechRecoveryRequest.objects.filter(pk=saved.pk).update(attempted_at=timezone.now() + timedelta(seconds=1), outcome=None)
            return rejected
        with patch("model_providers.recovery_admin.submit_recovery", side_effect=newer_attempt):
            self.client.post(detail, {"confirmed": "on"})
        saved.refresh_from_db()
        self.assertIsNone(saved.outcome)
        completed = self.result(saved)
        def concurrent_completion(_payload):
            SpeechRecoveryRequest.objects.filter(pk=saved.pk).update(completed=True, outcome=completed)
            return rejected
        with patch("model_providers.recovery_admin.submit_recovery", side_effect=concurrent_completion):
            self.client.post(detail, {"confirmed": "on"})
        saved.refresh_from_db()
        self.assertTrue(saved.completed)
        self.assertEqual(saved.outcome, completed)
