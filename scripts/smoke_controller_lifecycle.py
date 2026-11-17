"""Linux CI: real Docker, resource admission and routed traffic; fictional models.

This proves orchestration and rollback, not actual speech readiness or guest drain.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.admission import ControllerAdmission, ResourcePolicy
from speech_controller.docker_control import ApprovedContainer, DockerControls
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.lifecycle import Deployment, SwitchRequest
from speech_controller.readiness import ProbeTarget
from speech_controller.routing import RoutedTrafficGate, initialize_routing


def require(condition):
    if not condition:
        raise RuntimeError("Lifecycle fixture verification failed.")


def main():
    socket = "/var/run/docker.sock"
    prefix = ["docker", "--host", "unix://" + socket]
    image = subprocess.check_output(["docker", "compose", "images", "-q", "web"], text=True, timeout=10).strip()
    daemon = subprocess.check_output(prefix + ["info", "--format", "{{.ID}}"], text=True, timeout=10).strip()
    tag = "hotel-ai-lifecycle-" + uuid4().hex
    ids, approvals, deployments = [], [], []
    names = {key: tag + "-" + key for key in ("old", "new", "tts")}
    try:
        for key, name in names.items():
            container_id = subprocess.check_output(prefix + [
                "create", "--name", name, "--network", "none", "--memory", "32m", "--cpus", "0.25",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--label", "hotel-ai.controller-owner=ci-lifecycle", "--entrypoint", "python", image,
                "-c", "import time; time.sleep(180)",
            ], text=True, timeout=10).strip()
            ids.append(container_id)
            image_id = subprocess.check_output(prefix + ["inspect", "--format", "{{.Image}}", container_id],
                                               text=True, timeout=10).strip()
            approvals.append(ApprovedContainer(name, container_id, image_id, 250000000, 32 * 1048576, "ci-lifecycle"))
            deployments.append(Deployment(name, "tts" if key == "tts" else "stt", ("en", "pl")))
        controls = DockerControls(socket, daemon, approvals)
        controls.start(names["old"])
        controls.start(names["tts"])
        routes = {(language, task): names["old"] if task == "stt" else names["tts"]
                  for language in ("en", "pl") for task in ("stt", "tts")}
        failures = set()
        targets = tuple(ProbeTarget(item.name, item.task, "http://fixture.invalid", item.languages)
                        for item in deployments)

        policy = ResourcePolicy(daemon, "/", True, 1000000000, 128 * 1048576,
                                32 * 1048576, 32 * 1048576, ((approvals[0].image_id, 1048576),))
        admission = ControllerAdmission(controls, policy)

        def readiness(name, slot):
            return name not in failures and slot in routes and controls.inspect(name) == "running"

        with TemporaryDirectory() as directory:
            traffic_path = Path(directory) / "traffic.sqlite3"
            initialize_routing(traffic_path, targets, routes)
            traffic = RoutedTrafficGate(traffic_path, targets)
            path = Path(directory) / "controller.sqlite3"
            backend = DockerLifecycleBackend(controls, deployments, admission=admission, traffic=traffic,
                readiness=readiness, reconciliation=lambda current, proposed, pending, observations: True)
            # A valid but impossible CPU budget must reject before draining/starting.
            denied_policy = ResourcePolicy(daemon, "/", True, 1, 128 * 1048576,
                                           32 * 1048576, 32 * 1048576, policy.artifact_bytes)
            denied_backend = DockerLifecycleBackend(controls, deployments,
                admission=ControllerAdmission(controls, denied_policy), traffic=traffic,
                readiness=readiness, reconciliation=lambda *args: False)
            denied = RoutedLifecycleExecutor(denied_backend, deployments, routes,
                path).execute(SwitchRequest("denied", 0, ("en", "stt"), names["new"]))
            require(denied.reason == "admission_denied")
            with traffic.claim(("en", "stt")) as claim:
                require(claim.target.name == names["old"] and claim.route_revision == 0)
            require(controls.inspect(names["new"]) == "created" and controls.inspect(names["old"]) == "running")
            executor = RoutedLifecycleExecutor(backend, deployments, routes, path)
            first = executor.execute(SwitchRequest("en", 0, ("en", "stt"), names["new"]))
            require(first.status == "succeeded" and controls.inspect(names["old"]) == "running")
            require(traffic.state == first.state)
            with traffic.claim(("en", "stt")) as claim:
                require(claim.target.name == names["new"] and claim.route_revision == 1)
            with traffic.claim(("pl", "stt")) as claim:
                require(claim.target.name == names["old"])
            second_request = SwitchRequest("pl", 1, ("pl", "stt"), names["new"])
            second = executor.execute(second_request)
            require(second.status == "succeeded" and controls.inspect(names["old"]) == "exited")
            executor = RoutedLifecycleExecutor(backend, deployments, routes, path)
            require(executor.execute(second_request) == second)
            failures.add(names["old"])
            failed = executor.execute(SwitchRequest("rollback", 2, ("en", "stt"), names["old"]))
            require(failed.status == "rolled_back" and failed.state == second.state)
            require(controls.inspect(names["old"]) == "exited" and controls.inspect(names["new"]) == "running")
            require(traffic.state == second.state and not executor.recovery_status()["blocked"])
            for slot, target in second.state.routes:
                with traffic.claim(slot) as claim:
                    require(claim.target.name == target and claim.route_revision == 2)
            failures.clear()
            publish = traffic.publish

            def publish_then_interrupt(current, destination):
                require(publish(current, destination))
                raise RuntimeError("Fixture lost publication acknowledgement.")

            interrupted_request = SwitchRequest("publication-gap", 2, ("en", "stt"), names["old"])
            with patch.object(traffic, "publish", side_effect=publish_then_interrupt):
                interrupted = executor.execute(interrupted_request)
            require(interrupted.reason == "publication_uncertain" and executor.state.revision == 2)
            observed = traffic.state
            require(observed.revision == 3)
            executor = RoutedLifecycleExecutor(backend, deployments, routes, path)
            recovered = executor.recover("ci-recovery", 2, observed, "ci-operator", "Verified disposable fixture state")
            require(recovered.status == "reconciled" and recovered.state == observed)
            require(executor.recover("ci-recovery", 2, observed, "ci-operator", "Verified disposable fixture state") == recovered)
            require(executor.execute(interrupted_request) == recovered)
            require(executor.execute(second_request) == second)
            require(len(executor.reconciliation_history()) == 1 and not executor.recovery_status()["blocked"])
            for slot, target in observed.routes:
                with traffic.claim(slot) as claim:
                    require(claim.target.name == target and claim.route_revision == 3)
        print("Real routed switching, rollback and audited publication-gap recovery passed; fictional readiness/verifier only.")
    finally:
        errors = []
        for container_id in reversed(ids):
            try:
                subprocess.run(prefix + ["rm", "--force", container_id], check=True, timeout=10,
                               stdout=subprocess.DEVNULL)
            except subprocess.SubprocessError:
                errors.append(container_id)
        if errors:
            raise RuntimeError("Lifecycle fixture cleanup incomplete.")


if __name__ == "__main__":
    main()
