"""Linux CI: real Docker lifecycle, with fictional admission/traffic/model probes.

This proves orchestration and rollback, not actual speech readiness or guest drain.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.docker_control import ApprovedContainer, DockerControls
from speech_controller.journal import DurableLifecycleExecutor
from speech_controller.lifecycle import Deployment, SwitchRequest


def require(condition):
    if not condition:
        raise RuntimeError("Lifecycle fixture verification failed.")


class FixtureTraffic:
    def __init__(self):
        self.drained = set()

    def drain(self, slot):
        if slot in self.drained:
            return False
        self.drained.add(slot)
        return True

    def resume(self, slot):
        if slot not in self.drained:
            return False
        self.drained.remove(slot)
        return True


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
        traffic, failures = FixtureTraffic(), set()

        def admission(current, destination, observations):
            return set(observations) <= set(names.values()) and destination.revision == current.revision + 1

        def readiness(name, slot):
            return name not in failures and slot in routes and controls.inspect(name) == "running"

        backend = DockerLifecycleBackend(controls, deployments, admission=admission, traffic=traffic,
            readiness=readiness, reconciliation=lambda current, proposed, pending, observations: False)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "controller.sqlite3"
            executor = DurableLifecycleExecutor(backend, deployments, routes, path)
            first = executor.execute(SwitchRequest("en", 0, ("en", "stt"), names["new"]))
            require(first.status == "succeeded" and controls.inspect(names["old"]) == "running")
            second_request = SwitchRequest("pl", 1, ("pl", "stt"), names["new"])
            second = executor.execute(second_request)
            require(second.status == "succeeded" and controls.inspect(names["old"]) == "exited")
            executor = DurableLifecycleExecutor(backend, deployments, routes, path)
            require(executor.execute(second_request) == second)
            failures.add(names["old"])
            failed = executor.execute(SwitchRequest("rollback", 2, ("en", "stt"), names["old"]))
            require(failed.status == "rolled_back" and failed.state == second.state)
            require(controls.inspect(names["old"]) == "exited" and controls.inspect(names["new"]) == "running")
            require(not traffic.drained and not executor.recovery_status()["blocked"])
        print("Real lifecycle sharing, durable replay and readiness-failure rollback passed; fictional speech probes only.")
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
