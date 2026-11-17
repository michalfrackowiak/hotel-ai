from copy import deepcopy
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .switch_client import SwitchTransportError, submit_switch
from .test_control_status import TOKEN, snapshot
from .test_recovery_client import recovery_server


PAYLOAD = {"request_id": "admin-fixture", "expected_revision": 2, "slot": ["en", "stt"], "target": "new-worker"}


def outcome(status="succeeded", reason="switched", *, revision=3, target="new-worker"):
    selected = snapshot()["state"]
    selected["revision"] = revision
    selected["routes"][0][1] = target
    return dict(status=status, reason=reason, state=selected)


class SwitchClientTests(SimpleTestCase):
    def test_switch_posts_the_saved_payload_to_the_fixed_endpoint(self):
        result = outcome()
        with recovery_server(json.dumps(result).encode()) as calls:
            self.assertEqual(submit_switch(PAYLOAD), result)
        self.assertEqual(calls, [("/v1/switch", "Bearer " + TOKEN, PAYLOAD)])

    def test_success_and_cleanup_must_match_the_requested_slot_and_revision(self):
        for result in (outcome(revision=2), outcome(target="other"), outcome(reason="already_selected"),
                       outcome("cleanup_required", "old_worker_cleanup_failed", target="other")):
            with self.subTest(result=result), recovery_server(json.dumps(result).encode()):
                with self.assertRaises(SwitchTransportError):
                    submit_switch(PAYLOAD)

    def test_definitive_and_pending_controller_outcomes_preserve_their_meaning(self):
        for result in (outcome("reconciled", "operator_reconciled", revision=2, target="stt-worker"),
                       outcome("rolled_back", "replacement_failed", revision=2, target="stt-worker"),
                       outcome("recovery_required", "pending_reconciliation", revision=2, target="stt-worker"),
                       outcome("rejected", "stale_revision", revision=4, target="other"),
                       outcome("cleanup_required", "old_worker_cleanup_failed")):
            with self.subTest(status=result["status"]), recovery_server(json.dumps(result).encode()):
                self.assertEqual(submit_switch(PAYLOAD), result)

    def test_unconfirmed_post_can_be_retried_with_the_original_id(self):
        original = deepcopy(PAYLOAD)
        with recovery_server(status=503) as calls:
            with self.assertRaisesRegex(SwitchTransportError, "unconfirmed"):
                submit_switch(PAYLOAD)
            self.assertEqual(calls, [("/v1/switch", "Bearer " + TOKEN, original)])
        with recovery_server(json.dumps(outcome()).encode()) as calls:
            submit_switch(PAYLOAD)
            self.assertEqual(calls, [("/v1/switch", "Bearer " + TOKEN, original)])

    @override_settings(HOTEL_SPEECH_CONTROL_URL="http://127.0.0.1:8093", HOTEL_SPEECH_CONTROL_TOKEN=TOKEN)
    def test_invalid_slot_revision_or_target_cannot_reach_the_transport(self):
        for change in ({"slot": ["fr", "stt"]}, {"slot": "en,stt"}, {"expected_revision": True},
                       {"target": "http://example.test"}, {"actor": "admin"}):
            with self.subTest(change=change), patch("model_providers.switch_client.post_operation") as post:
                with self.assertRaises(SwitchTransportError):
                    submit_switch(PAYLOAD | change)
                post.assert_not_called()
