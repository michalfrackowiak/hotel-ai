from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.journal import DurableLifecycleExecutor
from speech_controller.lifecycle import Deployment, LifecycleExecutor, State, SwitchRequest


DEPLOYMENTS = (Deployment("old", "stt", ("en", "pl")), Deployment("new", "stt", ("en", "pl")),
               Deployment("tts", "tts", ("en", "pl")))
ROUTES = {(language, task): "old" if task == "stt" else "tts"
          for language in ("en", "pl") for task in ("stt", "tts")}


class Controls:
    approved_names = frozenset(("old", "new", "tts"))

    def __init__(self):
        self.states = {"old": "running", "new": "created", "tts": "running"}
        self.effects = []

    def inspect(self, name):
        return self.states[name]

    def start(self, name):
        self.effects.append(("start", name))
        self.states[name] = "running"

    def stop(self, name):
        self.effects.append(("stop", name))
        self.states[name] = "exited"


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.controls = Controls()
        self.admission = Mock(return_value=True)
        self.readiness = Mock(return_value=True)
        self.reconciliation = Mock(return_value=True)
        self.traffic = SimpleNamespace(drain=Mock(return_value=True), resume=Mock(return_value=True))
        self.backend = DockerLifecycleBackend(self.controls, DEPLOYMENTS, admission=self.admission,
            traffic=self.traffic, readiness=self.readiness, reconciliation=self.reconciliation)

    def execute(self):
        return LifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES).execute(SwitchRequest("one", 0, ("en", "stt"), "new"))

    def test_admission_denial_and_unowned_target_never_mutate(self):
        self.admission.return_value = 1
        self.assertEqual(self.execute().reason, "admission_denied")
        self.assertFalse(self.controls.effects)
        for name, state in (("old", "exited"), ("new", "running")):
            self.controls.states[name] = state
            self.admission.reset_mock()
            self.assertEqual(self.execute().reason, "admission_denied")
            self.admission.assert_not_called()
            self.controls.states[name] = "running" if name == "old" else "created"
        self.traffic.drain.assert_not_called()

    def test_running_container_requires_language_readiness_and_gate_confirmation(self):
        self.readiness.side_effect = lambda name, slot: name != "new"
        self.assertEqual(self.execute().status, "rolled_back")
        self.assertEqual(self.controls.effects, [("start", "new"), ("stop", "new")])
        self.readiness.assert_any_call("new", ("en", "stt"))
        self.controls.effects.clear()
        self.traffic.drain.return_value = False
        self.assertEqual(self.execute().status, "rolled_back")
        self.assertFalse(self.controls.effects)
        self.traffic.resume.return_value = False
        self.assertEqual(self.execute().status, "recovery_required")

    def test_durable_switch_sharing_and_replay(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            executor = DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, path)
            first = SwitchRequest("en", 0, ("en", "stt"), "new")
            self.assertEqual(executor.execute(first).status, "succeeded")
            self.assertEqual(self.controls.states["old"], "running")
            second = SwitchRequest("pl", 1, ("pl", "stt"), "new")
            result = executor.execute(second)
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(self.controls.effects, [("start", "new"), ("stop", "old")])
            restarted = DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, path)
            self.assertEqual(restarted.execute(second), result)
            self.assertEqual(len(self.controls.effects), 2)

    def test_reconciliation_requires_cleanup_all_models_and_external_verifier(self):
        state = State(0, tuple(sorted(ROUTES.items())))
        proposed = State(1, state.routes)
        self.controls.states["new"] = "running"
        self.assertFalse(self.backend.verify_reconciliation(state, proposed, ("pending",)))
        self.reconciliation.assert_not_called()
        self.controls.states["new"] = "exited"
        self.readiness.return_value = False
        self.assertFalse(self.backend.verify_reconciliation(state, proposed, ()))
        self.readiness.return_value = True
        self.reconciliation.return_value = 1
        self.assertFalse(self.backend.verify_reconciliation(state, proposed, ()))
        self.reconciliation.return_value = True
        self.assertTrue(self.backend.verify_reconciliation(state, proposed, ()))
        for slot, name in ROUTES.items():
            self.readiness.assert_any_call(name, slot)

    def test_integrations_and_approval_sets_are_mandatory(self):
        for change in ({"admission": None}, {"traffic": object()}, {"readiness": None},
                       {"reconciliation": None}, {"deployments": DEPLOYMENTS[:2]}):
            args = dict(controls=self.controls, deployments=DEPLOYMENTS, admission=self.admission,
                        traffic=self.traffic, readiness=self.readiness, reconciliation=self.reconciliation)
            with self.subTest(change=change), self.assertRaises(ValueError):
                DockerLifecycleBackend(**(args | change))


if __name__ == "__main__":
    unittest.main()
