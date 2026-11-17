"""Real route/journal stores with fictional controlled container effects."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.journal import DurableLifecycleExecutor, JournalUnavailable
from speech_controller.lifecycle import Deployment, LifecycleExecutor, State, SwitchRequest
from speech_controller.readiness import ProbeTarget
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from speech_controller.traffic import TrafficBusy


TARGETS = (ProbeTarget("old", "stt", "http://old:5001", ("en", "pl")),
           ProbeTarget("new", "stt", "http://new:5001", ("en", "pl")),
           ProbeTarget("tts", "tts", "http://tts:5000", ("en", "pl")))
DEPLOYMENTS = tuple(Deployment(item.name, item.task, item.languages) for item in TARGETS)
ROUTES = {(language, task): "old" if task == "stt" else "tts"
          for language in ("en", "pl") for task in ("stt", "tts")}
EN = ("en", "stt")
PL = ("pl", "stt")


class Crash(BaseException):
    pass


class Controls:
    approved_names = frozenset(("old", "new", "tts"))

    def __init__(self):
        self.states = dict(old="running", new="created", tts="running")
        self.effects = []

    def inspect(self, name):
        return self.states[name]

    def start(self, name):
        self.effects.append(("start", name))
        self.states[name] = "running"

    def stop(self, name):
        self.effects.append(("stop", name))
        self.states[name] = "exited"


class RoutedExecutorTests(unittest.TestCase):
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

    def request(self, name="en", revision=0, slot=EN, target="new"):
        return SwitchRequest(name, revision, slot, target)

    def assert_closed(self, slot=EN):
        with self.assertRaises(TrafficBusy):
            with self.gate.claim(slot):
                self.fail("uncertain traffic reopened")

    def test_checkpoint_precedes_cleanup_resume_and_durable_replay(self):
        observed = []
        real_resume, real_stop = self.gate.resume, self.controls.stop

        def check_resume(slot):
            status = self.executor.recovery_status()
            self.assertTrue(status["blocked"])
            self.assertTrue(status["pending_requests"])
            self.assertEqual(status["state"], self.gate.state)
            observed.append(("resume", status["state"].revision))
            return real_resume(slot)

        def check_stop(name):
            self.assertEqual(self.executor.state, self.gate.state)
            self.assertTrue(self.executor.recovery_status()["blocked"])
            observed.append(("stop", self.executor.state.revision))
            real_stop(name)

        with patch.object(self.gate, "resume", side_effect=check_resume), patch.object(self.controls, "stop", side_effect=check_stop):
            first = self.executor.execute(self.request())
            self.assertEqual(first.status, "succeeded")
            self.assertEqual(self.controls.states["old"], "running")
            second = self.executor.execute(self.request("pl", 1, PL))
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(observed, [("resume", 1), ("stop", 2), ("resume", 2)])
        effects = list(self.controls.effects)
        self.assertEqual(self.reopen().execute(self.request()), first)
        self.assertEqual(self.reopen().execute(self.request("pl", 1, PL)), second)
        self.assertEqual(self.controls.effects, effects)
        self.assertFalse(self.reopen().recovery_status()["blocked"])

    def test_prepublication_failure_checkpoints_old_route_before_resume(self):
        self.backend._readiness = lambda name, slot: name != "new"
        real_resume = self.gate.resume

        def resume(slot):
            status = self.executor.recovery_status()
            self.assertTrue(status["blocked"])
            self.assertEqual(status["state"].revision, 0)
            self.assertEqual(status["state"], self.gate.state)
            return real_resume(slot)

        with patch.object(self.gate, "resume", side_effect=resume):
            result = self.executor.execute(self.request())
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(self.controls.effects, [("start", "new"), ("stop", "new")])
        with self.gate.claim(EN) as claim:
            self.assertEqual(claim.target.name, "old")

    def assert_publication_uncertain(self, publish):
        with patch.object(self.gate, "publish", side_effect=publish), patch.object(self.gate, "resume") as resume:
            result = self.executor.execute(self.request())
        self.assertEqual(result.reason, "publication_uncertain")
        self.assertEqual(self.controls.effects, [("start", "new")])
        resume.assert_not_called()
        self.assert_closed()
        self.assertTrue(self.reopen().recovery_status()["blocked"])
        self.assertEqual(self.reopen().execute(self.request()).reason, "pending_reconciliation")

    def test_publication_false_never_cleans_or_resumes(self):
        self.assert_publication_uncertain(lambda *args: False)
        self.assertEqual(self.gate.state.revision, 0)

    def test_publication_exception_never_cleans_or_resumes(self):
        self.assert_publication_uncertain(RuntimeError("uncertain acknowledgement"))
        self.assertEqual(self.gate.state.revision, 0)

    def test_publication_commit_then_exception_never_cleans_or_resumes(self):
        real_publish = self.gate.publish

        def commit_then_raise(current, destination):
            self.assertTrue(real_publish(current, destination))
            raise RuntimeError("uncertain acknowledgement")

        self.assert_publication_uncertain(commit_then_raise)
        self.assertEqual(self.gate.state.revision, 1)
        self.assertEqual(self.reopen().state.revision, 0)

    def test_checkpoint_storage_failure_leaves_route_closed_and_pending(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON metadata BEGIN SELECT RAISE(ABORT,'fixture'); END")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            self.executor.execute(self.request())
        self.assertEqual(self.gate.state.revision, 1)
        self.assertEqual(self.reopen().state.revision, 0)
        self.assertEqual(self.controls.effects, [("start", "new")])
        self.assert_closed()
        self.assertTrue(self.reopen().recovery_status()["blocked"])

    def test_crash_after_checkpoint_never_replays_effects(self):
        with patch.object(self.gate, "resume", side_effect=Crash):
            with self.assertRaises(Crash):
                self.executor.execute(self.request())
        self.assertEqual(self.reopen().state, self.gate.state)
        self.assertEqual(self.reopen().state.revision, 1)
        effects = list(self.controls.effects)
        self.assertEqual(self.reopen().execute(self.request()).reason, "pending_reconciliation")
        self.assertEqual(self.reopen().execute(self.request("other", 1, PL)).reason, "reconciliation_required")
        self.assertEqual(self.controls.effects, effects)
        self.assert_closed()

    def test_resume_failure_recloses_and_final_storage_failure_keeps_checkpoint(self):
        real_resume = self.gate.resume

        def resume_then_raise(slot):
            real_resume(slot)
            raise RuntimeError("after reopening")

        with patch.object(self.gate, "resume", side_effect=resume_then_raise):
            result = self.executor.execute(self.request())
        self.assertEqual(result.status, "recovery_required")
        self.assertTrue(self.reopen().recovery_status()["blocked"])
        self.assert_closed()

    def test_final_outcome_write_failure_does_not_lose_published_checkpoint(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_finish BEFORE UPDATE ON operations BEGIN SELECT RAISE(ABORT,'fixture'); END")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            self.executor.execute(self.request())
        self.assertEqual(self.reopen().state, self.gate.state)
        self.assertTrue(self.reopen().recovery_status()["blocked"])
        with self.gate.claim(EN) as claim:
            self.assertEqual((claim.target.name, claim.route_revision), ("new", 1))
        effects = list(self.controls.effects)
        self.assertEqual(self.reopen().execute(self.request()).reason, "pending_reconciliation")
        self.assertEqual(self.controls.effects, effects)

    def test_old_worker_cleanup_failure_keeps_new_route_closed_and_blocked(self):
        self.assertEqual(self.executor.execute(self.request()).status, "succeeded")
        request = self.request("pl", 1, PL)
        with patch.object(self.controls, "stop", side_effect=RuntimeError("cleanup failed")) as stop:
            with patch.object(self.gate, "resume", wraps=self.gate.resume) as resume:
                result = self.executor.execute(request)
        self.assertEqual(result.status, "cleanup_required")
        stop.assert_called_once_with("old")
        resume.assert_not_called()
        self.assertEqual(self.reopen().state, self.gate.state)
        self.assertEqual(dict(result.state.routes)[PL], "new")
        self.assertEqual(result.state.revision, 2)
        self.assertEqual(self.controls.states["old"], "running")
        self.assert_closed(PL)
        self.assertTrue(self.reopen().recovery_status()["blocked"])
        self.assertEqual(self.reopen().execute(request), result)
        self.assertEqual(self.reopen().execute(self.request("third", 2, EN, "old")).reason,
                         "reconciliation_required")
        with self.gate.claim(EN) as claim:
            self.assertEqual(claim.target.name, "new")

    def test_backend_and_gate_catalogs_must_match_executor(self):
        changed = tuple(replace(item, languages=("en",)) if item.name == "new" else item
                        for item in DEPLOYMENTS)
        with self.assertRaises(ValueError):
            RoutedLifecycleExecutor(self.backend, changed, ROUTES, self.path)
        backend = DockerLifecycleBackend(self.controls, changed,
            admission=lambda *args: True, traffic=self.gate, readiness=lambda *args: True,
            reconciliation=lambda *args: True)
        with self.assertRaises(ValueError):
            RoutedLifecycleExecutor(backend, DEPLOYMENTS, ROUTES, self.path)
        self.assertFalse(self.controls.effects)

    def test_legacy_journal_and_replaced_route_store_are_rejected(self):
        plain_backend = DockerLifecycleBackend(self.controls, DEPLOYMENTS,
            admission=lambda *args: True,
            traffic=SimpleNamespace(drain=lambda slot: True, resume=lambda slot: True),
            readiness=lambda *args: True, reconciliation=lambda *args: True)
        legacy_path = self.path.with_name("legacy.sqlite3")
        DurableLifecycleExecutor(plain_backend, DEPLOYMENTS, ROUTES, legacy_path)
        with self.assertRaises(JournalUnavailable):
            RoutedLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, legacy_path)
        with self.assertRaises(JournalUnavailable):
            DurableLifecycleExecutor(plain_backend, DEPLOYMENTS, ROUTES, self.path)

        # Offline disposable fixture replacement, never an active-store repair.
        replacement = self.traffic_path.with_name("replacement.sqlite3")
        initialize_routing(replacement, TARGETS, ROUTES)
        replacement.replace(self.traffic_path)
        new_gate = RoutedTrafficGate(self.traffic_path, TARGETS)
        self.assertNotEqual(new_gate.binding, self.gate.binding)
        backend = DockerLifecycleBackend(self.controls, DEPLOYMENTS,
            admission=lambda *args: True, traffic=new_gate, readiness=lambda *args: True,
            reconciliation=lambda *args: True)
        with self.assertRaises(JournalUnavailable):
            RoutedLifecycleExecutor(backend, DEPLOYMENTS, ROUTES, self.path)
        self.assertFalse(self.controls.effects)

    def test_mismatched_routes_and_legacy_execution_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, self.path)
        with self.assertRaises(ValueError):
            LifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES).execute(self.request())
        with self.assertRaises(JournalUnavailable):
            self.executor.reconcile("recover", 0, ROUTES, "operator", "fixture")
        self.assertTrue(self.gate.drain(EN))
        changed = State(1, tuple(sorted((ROUTES | {EN: "new"}).items())))
        self.assertTrue(self.gate.publish(self.gate.state, changed))
        result = self.executor.execute(self.request())
        self.assertEqual(result.reason, "route_state_mismatch")
        self.assertFalse(self.controls.effects)
        self.assert_closed()
        self.assertTrue(self.reopen().recovery_status()["blocked"])


if __name__ == "__main__":
    unittest.main()
