"""Coordinated recovery uses real durable stores and fictional worker effects."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.journal import JournalUnavailable
from speech_controller.lifecycle import SwitchRequest
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from test_controller_routed_executor import Controls, Crash, TARGETS, DEPLOYMENTS, ROUTES, EN, PL


class RoutedRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "journal.sqlite3"
        self.traffic_path = Path(directory.name) / "traffic.sqlite3"
        initialize_routing(self.traffic_path, TARGETS, ROUTES)
        self.gate = RoutedTrafficGate(self.traffic_path, TARGETS)
        self.controls = Controls()
        self.backend = DockerLifecycleBackend(self.controls, DEPLOYMENTS,
            admission=lambda *args: True, traffic=self.gate, readiness=lambda *args: True,
            reconciliation=lambda *args: True)
        self.executor = self.reopen()

    def reopen(self):
        return RoutedLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, self.path)

    def slots(self):
        with closing(sqlite3.connect(self.traffic_path)) as db:
            return db.execute("SELECT closed FROM slots ORDER BY language,task").fetchall()

    def pending(self, publish=False):
        request = SwitchRequest("interrupted", 0, EN, "new")
        with patch.object(self.executor, "_checkpoint" if publish else "_run_pending", side_effect=Crash):
            with self.assertRaises(Crash):
                self.executor.execute(request)
        return request

    def recover(self, name="recovery", revision=0, observed=None):
        return self.executor.recover(name, revision, observed or self.gate.state, "operator", "Verified workers")

    def test_publication_gap_adopts_exact_routes_with_audit_before_resume(self):
        request = self.pending(publish=True)
        prior, observed = self.executor.state, self.gate.state
        self.assertNotEqual(prior, observed)
        real_resume = self.gate.resume_all

        def resume(state):
            status = self.executor.recovery_status()
            self.assertEqual(status["pending_recoveries"], ("recovery",))
            self.assertEqual(status["pending_requests"], (request.request_id,))
            self.assertEqual(status["state"], observed)
            self.assertTrue(status["blocked"])
            self.assertEqual(self.slots(), [(1,)] * 4)
            return real_resume(state)

        with patch.object(self.gate, "resume_all", side_effect=resume):
            result = self.recover()
        self.assertEqual(result.status, "reconciled")
        self.assertEqual(self.executor.state, observed)
        self.assertEqual(self.gate.state, observed)
        self.assertFalse(self.executor.recovery_status()["blocked"])
        self.assertEqual(self.slots(), [(0,)] * 4)
        history = self.executor.reconciliation_history()
        self.assertEqual(history[0]["prior_state"], prior)
        self.assertEqual(history[0]["pending_requests"], (request.request_id,))
        self.assertEqual(self.executor.execute(request), result)
        with patch.object(self.gate, "drain_all", side_effect=AssertionError("replay mutated admission")):
            self.assertEqual(self.reopen().recover("recovery", 0, observed, "operator", "Verified workers"), result)

    def test_same_state_recovery_preserves_completed_history(self):
        completed = SwitchRequest("unchanged", 0, EN, "old")
        result = self.executor.execute(completed)
        self.pending()
        state = self.executor.state
        self.assertEqual(self.recover().state, state)
        self.assertEqual(self.executor.execute(completed), result)
        self.assertEqual(self.controls.effects, [])

    def test_active_and_uncertain_claims_remain_and_block_recovery(self):
        self.pending()
        self.gate._timeout = 0.5
        with self.gate.claim(PL) as claim:
            self.assertEqual(self.recover().reason, "routed_drain_unverified")
            self.assertEqual(self.slots(), [(1,)] * 4)
            claim.mark_uncertain()
        with closing(sqlite3.connect(self.traffic_path)) as db:
            claims = db.execute("SELECT * FROM claims").fetchall()
        self.assertEqual(self.recover().reason, "routed_drain_unverified")
        with closing(sqlite3.connect(self.traffic_path)) as db:
            self.assertEqual(db.execute("SELECT * FROM claims").fetchall(), claims)
        self.assertEqual(self.executor.reconciliation_history(), ())

    def test_unreferenced_workers_readiness_and_external_verifier_deny(self):
        self.pending()
        self.controls.states["new"] = "running"
        self.assertEqual(self.recover().reason, "reconciliation_unverified")
        self.controls.states["new"] = "exited"
        with patch.object(self.backend, "_readiness", return_value=False):
            self.assertEqual(self.recover().reason, "reconciliation_unverified")
        for answer in (False, 1):
            with patch.object(self.backend, "_reconciliation", return_value=answer):
                self.assertEqual(self.recover().reason, "reconciliation_unverified")
        self.assertEqual(self.slots(), [(1,)] * 4)
        self.assertEqual(self.executor.reconciliation_history(), ())
        self.assertEqual(self.controls.effects, [])

    def test_stale_or_conflicting_snapshot_has_no_admission_effect(self):
        self.pending()
        state = self.gate.state
        wrong_routes = replace(state, routes=tuple(sorted((ROUTES | {EN: "new"}).items())))
        for revision, observed, reason in ((1, state, "stale_revision"),
                (0, wrong_routes, "route_state_conflict"),
                (0, replace(state, revision=1), "routed_drain_unverified")):
            self.assertEqual(self.recover(revision=revision, observed=observed).reason, reason)
            self.assertEqual(self.slots(), [(0,)] * 4)
        self.assertEqual(self.executor.reconciliation_history(), ())

    def test_invalid_requests_and_healthy_journal_leave_admission_unchanged(self):
        state = self.gate.state
        valid = ["recovery", 0, state, "operator", "Verified workers"]
        for index, invalid in ((0, ""), (1, True), (1, -1), (2, None),
                (2, replace(state, revision=True)), (2, replace(state, revision=2**63)),
                (3, " "), (3, "x" * 121), (4, "line\nbreak"), (4, "x" * 501)):
            arguments = valid.copy()
            arguments[index] = invalid
            with self.subTest(index=index, invalid=invalid):
                with self.assertRaises(ValueError):
                    self.executor.recover(*arguments)
        self.assertEqual(self.recover().reason, "reconciliation_not_required")
        self.assertEqual(self.slots(), [(0,)] * 4)
        self.assertEqual(self.executor.reconciliation_history(), ())

    def test_older_observation_cannot_roll_back_checkpointed_revision(self):
        prior = self.gate.state
        with patch.object(self.backend, "resume", side_effect=Crash):
            with self.assertRaises(Crash):
                self.executor.execute(SwitchRequest("interrupted", 0, EN, "new"))
        self.assertEqual(self.executor.state.revision, 1)
        slots = self.slots()
        self.assertEqual(self.recover(revision=1, observed=prior).reason, "route_state_conflict")
        self.assertEqual(self.slots(), slots)
        self.assertEqual(self.executor.reconciliation_history(), ())

    def test_checkpoint_failure_keeps_original_journal_and_closed_slots(self):
        self.pending(publish=True)
        prior = self.executor.state
        self.trigger("checkpoint", "BEFORE INSERT ON reconciliations")
        with self.assertRaises(JournalUnavailable):
            self.recover()
        self.assertEqual(self.executor.state, prior)
        self.assertEqual(self.executor.reconciliation_history(), ())
        self.assertTrue(self.executor.recovery_status()["pending_requests"])
        self.assertEqual(self.slots(), [(1,)] * 4)
        self.drop_trigger("checkpoint")
        self.assertEqual(self.recover().status, "reconciled")

    def test_final_storage_failure_restart_reverifies_original_audit_context(self):
        request = self.pending(publish=True)
        prior, observed = self.executor.state, self.gate.state
        self.trigger("final_write", "BEFORE UPDATE OF outcome ON reconciliations")
        with self.assertRaises(JournalUnavailable):
            self.recover()
        self.assertEqual(self.executor.state, observed)
        self.assertEqual(self.slots(), [(0,)] * 4)
        status = self.executor.recovery_status()
        self.assertTrue(status["blocked"])
        self.assertEqual(status["pending_requests"], (request.request_id,))
        self.assertEqual(status["pending_recoveries"], ("recovery",))
        self.assertEqual(self.executor.execute(SwitchRequest("blocked", 1, PL, "new")).reason, "reconciliation_required")
        self.drop_trigger("final_write")
        self.executor = self.reopen()
        verified = []

        def verify(current, selected, pending, workers):
            self.assertEqual(self.slots(), [(1,)] * 4)
            verified.append((current, selected, pending))
            return True

        with patch.object(self.backend, "_reconciliation", side_effect=verify):
            self.assertEqual(self.recover().status, "reconciled")
        self.assertEqual(verified, [(prior, observed, (request.request_id,))])
        self.assertEqual(self.executor.reconciliation_history()[0]["prior_state"], prior)

    def test_pending_recovery_rejects_other_ids_and_conflicting_payload_without_effects(self):
        self.pending(publish=True)
        with patch.object(self.gate, "resume_all", side_effect=Crash):
            with self.assertRaises(Crash):
                self.recover()
        with patch.object(self.gate, "drain_all", side_effect=AssertionError("rejected request changed admission")):
            self.assertEqual(self.recover(name="other", revision=1).reason, "routed_recovery_in_progress")
            self.assertEqual(self.executor.recover("recovery", 0, self.gate.state, "other", "Verified workers").reason,
                             "recovery_id_conflict")
        self.executor = self.reopen()
        self.assertEqual(self.recover().status, "reconciled")

    def test_resume_exception_after_commit_recloses_and_retry_reverifies(self):
        self.pending()
        real_resume = self.gate.resume_all

        def resume(state):
            self.assertTrue(real_resume(state))
            raise RuntimeError("acknowledgment lost")

        with patch.object(self.gate, "resume_all", side_effect=resume):
            self.assertEqual(self.recover().reason, "routed_recovery_pending")
        self.assertEqual(self.slots(), [(1,)] * 4)
        with patch.object(self.backend, "_reconciliation", return_value=False):
            self.assertEqual(self.recover().reason, "reconciliation_unverified")
        self.assertEqual(self.executor.recovery_status()["pending_recoveries"], ("recovery",))
        self.assertEqual(self.recover().status, "reconciled")

    def test_process_crash_after_resume_keeps_checkpoint_for_restart(self):
        self.pending()
        real_resume = self.gate.resume_all

        def resume(state):
            self.assertTrue(real_resume(state))
            raise Crash

        with patch.object(self.gate, "resume_all", side_effect=resume):
            with self.assertRaises(Crash):
                self.recover()
        self.assertEqual(self.slots(), [(0,)] * 4)
        self.executor = self.reopen()
        self.assertTrue(self.executor.recovery_status()["blocked"])
        self.assertEqual(self.recover().status, "reconciled")

    def test_recovery_requires_lock_and_keeps_both_schemas(self):
        self.pending()
        def schema(path):
            with closing(sqlite3.connect(path)) as db:
                return db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
        before = (schema(self.path), schema(self.traffic_path))
        with self.executor._exclusive() as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.recover().reason, "busy")
            self.assertEqual(self.slots(), [(0,)] * 4)
        self.assertEqual(self.recover().status, "reconciled")
        self.assertEqual((schema(self.path), schema(self.traffic_path)), before)
        with self.assertRaises(JournalUnavailable):
            self.executor.reconcile("legacy", 0, ROUTES, "operator", "Not coordinated")

    def trigger(self, name, clause):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(f"CREATE TRIGGER {name} {clause} BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END")
            db.commit()

    def drop_trigger(self, name):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(f"DROP TRIGGER {name}")
            db.commit()

    def test_all_slot_helpers_require_exact_state_and_empty_closed_admission(self):
        self.gate._timeout = 0.5
        state = self.gate.state
        self.assertFalse(self.gate.resume_all(state))
        self.assertFalse(self.gate.drain_all(replace(state, revision=1)))
        self.assertEqual(self.slots(), [(0,)] * 4)
        with self.gate.claim(PL):
            self.assertFalse(self.gate.drain_all(state))
            self.assertEqual(self.slots(), [(1,)] * 4)
            self.assertFalse(self.gate.resume_all(state))
        self.assertTrue(self.gate.quiescent(state))
        self.assertFalse(self.gate.resume_all(replace(state, revision=1)))
        self.assertTrue(self.gate.resume_all(state))
        self.assertEqual(self.slots(), [(0,)] * 4)


if __name__ == "__main__":
    unittest.main()
