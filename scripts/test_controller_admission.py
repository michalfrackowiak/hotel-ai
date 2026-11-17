"""Fresh resource gates and lifecycle effects using fictional native-host readings."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.admission import ControllerAdmission, MIB, ResourcePolicy
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.docker_control import ApprovedContainer, ControlUnavailable, DockerControls
from speech_controller.lifecycle import Deployment, LifecycleExecutor, State, SwitchRequest


IMAGE = "sha256:" + "a" * 64
NEW_IMAGE = "sha256:" + "b" * 64
DEPLOYMENTS = tuple(Deployment(name, "tts" if name == "tts" else "stt", ("en", "pl"))
                    for name in ("old", "new", "tts", "spare"))
ROUTES = {(language, task): "tts" if task == "tts" else "old"
          for language in ("en", "pl") for task in ("stt", "tts")}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.states = {"old": "running", "new": "created", "tts": "running", "spare": "exited"}
        approvals = tuple(ApprovedContainer(name, str(index) * 64, NEW_IMAGE if name == "new" else IMAGE,
                                            250000000, 32 * MIB, "fixture")
                          for index, name in enumerate(self.states, 1))
        with patch.object(DockerControls, "_check_socket"):
            self.controls = DockerControls(str(Path.cwd() / "fixture.sock"), "fixture-daemon", approvals)
        self.controls.inspect = Mock(side_effect=self.states.__getitem__)
        self.controls.start, self.controls.stop = Mock(), Mock()
        self.policy = ResourcePolicy("fixture-daemon", str(Path.cwd()), True, 750000000,
                                     96 * MIB, 32 * MIB, 32 * MIB, ((IMAGE, 8 * MIB), (NEW_IMAGE, 8 * MIB)))
        self.gate = ControllerAdmission(self.controls, self.policy)
        self.current = State(0, tuple(sorted(ROUTES.items())))
        self.destination = State(1, tuple(sorted((ROUTES | {("en", "stt"): "new"}).items())))
        self.resources = dict(scope="controller_host", platform="linux", cpu_count=2,
                              memory_total_mib=1024, memory_available_mib=128,
                              disk_total_mib=1024, disk_free_mib=48)
        self.collector = patch("speech_controller.admission.host_resources", side_effect=lambda _path: self.resources.copy())
        self.collect = self.collector.start()
        self.addCleanup(self.collector.stop)

    def admitted(self):
        return self.gate(self.current, self.destination, self.states.copy())

    def backend(self, gate=None):
        self.traffic = SimpleNamespace(drain=Mock(return_value=True), resume=Mock(return_value=True))
        return DockerLifecycleBackend(self.controls, DEPLOYMENTS, admission=gate or self.gate,
                                      traffic=self.traffic, readiness=Mock(return_value=True),
                                      reconciliation=Mock(return_value=False))

    def test_shared_deployments_and_images_count_once_at_exact_limits(self):
        self.assertTrue(self.admitted())
        self.assertEqual(self.controls.inspect.call_count, 4)
        self.collect.assert_called_once_with(self.policy.storage_path)
        self.states["spare"] = "running"
        self.assertFalse(self.admitted())
        self.gate = ControllerAdmission(self.controls, replace(self.policy, cpu_budget_nanos=1000000000,
                                                               memory_budget_bytes=128 * MIB))
        self.resources["memory_available_mib"] = 160
        self.assertTrue(self.admitted())

    def test_each_resource_shortfall_denies_before_any_effect(self):
        for change in ({"memory_available_mib": 127.99}, {"disk_free_mib": 47.99}, {"cpu_count": 0}):
            with self.subTest(change=change):
                original = self.resources.copy()
                self.resources.update(change)
                executor = LifecycleExecutor(self.backend(), DEPLOYMENTS, ROUTES)
                self.assertEqual(executor.execute(SwitchRequest("denied", 0, ("en", "stt"), "new")).reason,
                                 "admission_denied")
                self.traffic.drain.assert_not_called()
                self.controls.start.assert_not_called()
                self.controls.stop.assert_not_called()
                self.resources = original
        for change in ({"cpu_budget_nanos": 749999999}, {"memory_budget_bytes": 96 * MIB - 1}):
            gate = ControllerAdmission(self.controls, replace(self.policy, **change))
            self.assertFalse(gate(self.current, self.destination, self.states))

    def test_retiring_worker_has_no_stop_credit_and_cpu_count_is_enforced(self):
        self.current = State(1, tuple(sorted((ROUTES | {("pl", "stt"): "new"}).items())))
        self.destination = State(2, tuple(sorted((dict(self.current.routes) | {("en", "stt"): "new"}).items())))
        self.states["new"] = "running"
        self.assertTrue(self.admitted())
        self.gate = ControllerAdmission(self.controls, replace(self.policy, memory_budget_bytes=64 * MIB))
        self.assertFalse(self.admitted())
        # Configured policy can be larger than hardware; observed CPU is a separate gate.
        approvals = tuple(replace(item, nano_cpus=1000000000) for item in self.controls.approvals)
        with patch.object(DockerControls, "_check_socket"):
            controls = DockerControls(str(Path.cwd() / "cpu.sock"), "fixture-daemon", approvals)
        controls.inspect = self.controls.inspect
        gate = ControllerAdmission(controls, replace(self.policy, cpu_budget_nanos=4000000000))
        self.assertFalse(gate(self.current, self.destination, self.states))

    def test_resource_loss_during_drain_rolls_back_without_start_or_stop(self):
        backend = self.backend()
        def drain(_slot):
            self.resources["memory_available_mib"] = 127
            return True
        self.traffic.drain.side_effect = drain
        executor = LifecycleExecutor(backend, DEPLOYMENTS, ROUTES)
        result = executor.execute(SwitchRequest("changed", 0, ("en", "stt"), "new"))
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.state, self.current)
        self.assertEqual(self.collect.call_count, 2)
        self.controls.start.assert_not_called()
        self.controls.stop.assert_not_called()
        self.traffic.resume.assert_called_once_with(("en", "stt"))

    def test_incomplete_or_stale_states_and_inspection_failure_deny(self):
        for observations in ({}, self.states | {"extra": "running"}, self.states | {"spare": "running"}):
            self.assertFalse(self.gate(self.current, self.destination, observations))
        self.collect.assert_not_called()
        self.controls.inspect.side_effect = ControlUnavailable
        self.assertFalse(self.admitted())
        self.collect.assert_not_called()

    def test_invalid_and_unknown_host_metrics_fail_closed(self):
        for key, value in (("scope", "container"), ("platform", "unsupported"),
                           ("cpu_count", True), ("cpu_count", 1.5),
                           ("memory_available_mib", None), ("memory_available_mib", float("nan")),
                           ("memory_available_mib", float("inf")), ("memory_available_mib", True),
                           ("memory_available_mib", 1025), ("memory_total_mib", 0),
                           ("disk_free_mib", -1), ("disk_free_mib", "48"),
                           ("disk_free_mib", 1025), ("disk_total_mib", 0)):
            with self.subTest(key=key, value=value):
                original = self.resources[key]
                self.resources[key] = value
                self.assertFalse(self.admitted())
                self.resources[key] = original
        self.collect.side_effect = OSError
        self.assertFalse(self.admitted())
        self.collect.side_effect = None
        for value in (None, [], {}, {"scope": "controller_host", "platform": "linux"}):
            self.collect.return_value = value
            self.assertFalse(self.admitted())

    def test_policy_and_same_controls_binding_are_required(self):
        for change in ({"topology_confirmed": False}, {"storage_path": "relative"},
                       {"cpu_budget_nanos": True}, {"memory_reserve_bytes": 0},
                       {"disk_reserve_bytes": -1}, {"artifact_bytes": []},
                       {"artifact_bytes": ((IMAGE, 1), (IMAGE, 2))}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.policy, **change)
        with self.assertRaises(FrozenInstanceError):
            self.policy.memory_budget_bytes = 1
        for policy in (replace(self.policy, daemon_id="other"), replace(self.policy, artifact_bytes=((IMAGE, 1),))):
            with self.assertRaises(ValueError):
                ControllerAdmission(self.controls, policy)
        with patch.object(DockerControls, "_check_socket"):
            other = DockerControls(str(Path.cwd() / "other.sock"), "fixture-daemon", self.controls.approvals)
        with self.assertRaises(ValueError):
            self.backend(ControllerAdmission(other, self.policy))

    def test_invalid_routes_or_revisions_deny(self):
        for state in (State(2, self.destination.routes), State(True, self.destination.routes),
                      State(1, self.destination.routes[:-1]), State(1, (("unknown", "new"),))):
            self.assertFalse(self.gate(self.current, state, self.states))
        self.controls.inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
