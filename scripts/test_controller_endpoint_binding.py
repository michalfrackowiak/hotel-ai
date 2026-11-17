"""Endpoint identity tests use projected Docker fixtures, never a real daemon."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.docker_control import ControlUnavailable, DockerControls
from speech_controller.endpoint_binding import BoundReadiness, EndpointBinding
from speech_controller.lifecycle import Deployment
from speech_controller.readiness import ProbeTarget, ReadinessProbes
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from test_controller_docker_control import APPROVED, INFO, controls, output, record


NETWORK = "c" * 64
ADDRESS = "172.20.0.2"
NETWORK_RECORD = dict(id=NETWORK, driver="bridge", scope="local")
TARGET = ProbeTarget("fixture", "tts", "http://172.20.0.2:5000", ("en", "pl"))
BINDING = EndpointBinding("fixture", NETWORK, ADDRESS)


def endpoint_record(**changes):
    return record("running", network_mode="private-fixture",
                  networks=[dict(id=NETWORK, address=ADDRESS)]) | changes


class EndpointControlTests(unittest.TestCase):
    def test_network_inspection_shares_deadline_and_cannot_start_after_expiry(self):
        with controls() as control, patch("speech_controller.docker_control.monotonic", side_effect=[100, 101, 104, 108]), \
                patch("speech_controller.docker_control.subprocess.run", side_effect=[
                    output(INFO), output(endpoint_record()), output(NETWORK_RECORD)]) as run:
            self.assertIs(control.verify_endpoint("fixture", NETWORK, ADDRESS), True)
            self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [9, 6, 2])
        with controls() as control, patch("speech_controller.docker_control.monotonic", side_effect=[100, 101, 104, 111]), \
                patch("speech_controller.docker_control.subprocess.run", side_effect=[
                    output(INFO), output(endpoint_record())]) as run:
            with self.assertRaises(ControlUnavailable):
                control.verify_endpoint("fixture", NETWORK, ADDRESS)
            self.assertEqual(run.call_count, 2)

    def test_exact_running_container_bridge_and_address_use_fixed_read_only_commands(self):
        with controls() as control, patch("speech_controller.docker_control.subprocess.run", side_effect=[
                output(INFO), output(endpoint_record()), output(NETWORK_RECORD)]) as run:
            self.assertIs(control.verify_endpoint("fixture", NETWORK, ADDRESS), True)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[1].args[0][-1], APPROVED.container_id)
            self.assertEqual(run.call_args.args[0][3:5], ["network", "inspect"])
            self.assertEqual(run.call_args.args[0][-1], NETWORK)
            for call in run.call_args_list:
                self.assertFalse(call.kwargs["shell"])
                self.assertLessEqual(call.kwargs["timeout"], 10)

    def test_endpoint_identity_or_topology_drift_fails_before_network_inspect(self):
        changes = [dict(id="d" * 64), dict(state="exited"), dict(image_id="sha256:" + "d" * 64),
                   dict(network_mode="host"), dict(network_mode="none"), dict(network_mode="container:other"),
                   dict(networks=None), dict(networks=[]), dict(networks=[dict(id=NETWORK, address="172.20.0.3")]),
                   dict(networks=[dict(id="d" * 64, address=ADDRESS)]),
                   dict(networks=[dict(id=NETWORK, address=ADDRESS)] * 2)]
        for change in changes:
            with self.subTest(change=change), controls() as control, \
                    patch("speech_controller.docker_control.subprocess.run", side_effect=[
                        output(INFO), output(endpoint_record(**change))]) as run:
                with self.assertRaises(ControlUnavailable):
                    control.verify_endpoint("fixture", NETWORK, ADDRESS)
                self.assertEqual(run.call_count, 2)

    def test_network_inspection_must_match_pinned_local_bridge(self):
        for change in (dict(id="d" * 64), dict(driver="overlay"), dict(scope="swarm"), dict(extra="secret")):
            with self.subTest(change=change), controls() as control, \
                    patch("speech_controller.docker_control.subprocess.run", side_effect=[
                        output(INFO), output(endpoint_record()), output(NETWORK_RECORD | change)]):
                with self.assertRaises(ControlUnavailable) as caught:
                    control.verify_endpoint("fixture", NETWORK, ADDRESS)
                self.assertNotIn("secret", str(caught.exception))

    def test_invalid_binding_and_wrong_daemon_never_inspect_container(self):
        with controls() as control, patch("speech_controller.docker_control.subprocess.run") as run:
            for network, address in (("short", ADDRESS), (NETWORK, "127.0.0.1"), (NETWORK, "169.254.1.1"),
                    (NETWORK, "8.8.8.8"), (NETWORK, "localhost"), (NETWORK, "172.020.0.2"), (NETWORK, 1)):
                with self.assertRaises(ControlUnavailable):
                    control.verify_endpoint("fixture", network, address)
            run.assert_not_called()
        with controls() as control, patch("speech_controller.docker_control.subprocess.run",
                return_value=output(INFO | {"id": "wrong"})) as run:
            with self.assertRaises(ControlUnavailable):
                control.verify_endpoint("fixture", NETWORK, ADDRESS)
            self.assertEqual(run.call_count, 1)


class BoundReadinessTests(unittest.TestCase):
    def test_binding_and_catalog_configuration_reject_ambiguous_endpoints(self):
        for change in (dict(name="bad name"), dict(network_id="short"), dict(address="127.0.0.1"),
                       dict(address="192.0.0.1"), dict(address="172.16.01.2"), dict(address="::1")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(BINDING, **change)
        with self.assertRaises(FrozenInstanceError):
            BINDING.address = "172.20.0.3"
        with controls() as control:
            for url in ("http://172.20.0.3:5000", "http://fixture:5000", "http://172.20.0.2",
                        "https://172.20.0.2:5000", "http://172.20.0.2:5000/", "http://172.20.0.2:05000"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    BoundReadiness(control, [replace(TARGET, base_url=url)], [BINDING])
            for targets, bindings in (([], []), ([TARGET, TARGET], [BINDING, BINDING]),
                    ([TARGET], []), ([replace(TARGET, name="other")], [BINDING])):
                with self.assertRaises(ValueError):
                    BoundReadiness(control, targets, bindings)

    def test_valid_binding_surrounds_real_probe_interface_in_order(self):
        events = []
        with controls() as control:
            ready = BoundReadiness(control, [TARGET], [BINDING])
            with patch.object(control, "verify_endpoint", side_effect=lambda *args: events.append("binding") or True) as verify, \
                    patch.object(ReadinessProbes, "__call__", side_effect=lambda *args: events.append("probe") or True):
                self.assertIs(ready("fixture", ("pl", "tts")), True)
                self.assertEqual(events, ["binding", "probe", "binding"])
                self.assertEqual(verify.call_args.args, ("fixture", NETWORK, ADDRESS))
            self.assertIs(ready.controls, control)
            self.assertEqual(ready.targets, (TARGET,))

    def test_failed_precheck_never_probes_and_failed_postcheck_rejects_readiness(self):
        with controls() as control:
            ready = BoundReadiness(control, [TARGET], [BINDING])
            for answer in (False, 1, ControlUnavailable("fixture")):
                with self.subTest(answer=answer), patch.object(control, "verify_endpoint", side_effect=[answer]), \
                        patch.object(ReadinessProbes, "__call__") as probe:
                    self.assertIs(ready("fixture", ("en", "tts")), False)
                    probe.assert_not_called()
            for answer in (False, 1, ControlUnavailable("changed binding")):
                with self.subTest(answer=answer), patch.object(control, "verify_endpoint", side_effect=[True, answer]), \
                        patch.object(ReadinessProbes, "__call__", return_value=True):
                    self.assertIs(ready("fixture", ("en", "tts")), False)
            with patch.object(control, "verify_endpoint", return_value=True) as verify, \
                    patch.object(ReadinessProbes, "__call__", return_value=False):
                self.assertIs(ready("fixture", ("en", "tts")), False)
                self.assertEqual(verify.call_count, 1)

    def test_unknown_slot_or_target_never_probes_or_inspects(self):
        with controls() as control:
            ready = BoundReadiness(control, [TARGET], [BINDING])
            with patch.object(control, "verify_endpoint") as verify, patch.object(ReadinessProbes, "__call__") as probe:
                for name, slot in (("other", ("en", "tts")), ("fixture", ("de", "tts")),
                        ("fixture", ("en", "stt")), ("fixture", ["en", "tts"]), (None, None)):
                    self.assertIs(ready(name, slot), False)
                verify.assert_not_called()
                probe.assert_not_called()

    def test_duplicate_addresses_cannot_cross_networks_or_share_endpoint(self):
        with controls():
            extra = replace(APPROVED, name="other", container_id="d" * 64)
            control = DockerControls("/private/docker.sock", "fixture-daemon", [APPROVED, extra])
            for target, binding in ((replace(TARGET, name="other"), replace(BINDING, name="other")),
                    (replace(TARGET, name="other", base_url="http://172.20.0.2:5001"),
                     replace(BINDING, name="other", network_id="e" * 64))):
                with self.assertRaises(ValueError):
                    BoundReadiness(control, [TARGET, target], [BINDING, binding])

    def test_backend_requires_same_controls_deployments_and_routed_urls(self):
        with TemporaryDirectory() as directory, controls():
            extra = replace(APPROVED, name="asr", container_id="d" * 64)
            control = DockerControls("/private/docker.sock", "fixture-daemon", [APPROVED, extra])
            targets = (TARGET, ProbeTarget("asr", "stt", "http://172.20.0.3:5001", ("en", "pl")))
            bindings = (BINDING, EndpointBinding("asr", NETWORK, "172.20.0.3"))
            ready = BoundReadiness(control, targets, bindings)
            deployments = tuple(Deployment(item.name, item.task, item.languages) for item in targets)
            routes = {(lang, task): "asr" if task == "stt" else "fixture"
                      for lang in ("en", "pl") for task in ("stt", "tts")}
            for index, catalog in enumerate((targets, (replace(TARGET, base_url="http://172.20.0.4:5000"), targets[1]))):
                path = Path(directory) / f"routes-{index}.sqlite3"
                initialize_routing(path, catalog, routes)
                traffic = RoutedTrafficGate(path, catalog)
                args = dict(admission=lambda *args: True, traffic=traffic, readiness=ready, reconciliation=lambda *args: True)
                if index:
                    with self.assertRaises(ValueError):
                        DockerLifecycleBackend(control, deployments, **args)
                else:
                    self.assertIs(DockerLifecycleBackend(control, deployments, **args).routed_traffic, traffic)
                    other = DockerControls("/private/docker.sock", "fixture-daemon", [APPROVED, extra])
                    with self.assertRaises(ValueError):
                        DockerLifecycleBackend(other, deployments, **args)
                    changed = (replace(deployments[0], languages=("en",)), deployments[1])
                    with self.assertRaises(ValueError):
                        DockerLifecycleBackend(control, changed, **args)


if __name__ == "__main__":
    unittest.main()
