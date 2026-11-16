"""Linux CI only: start/stop a disposable isolated container, then remove it."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.docker_control import ApprovedContainer, DockerControls


def main():
    socket = "/var/run/docker.sock"
    prefix = ["docker", "--host", "unix://" + socket]
    image = subprocess.check_output(["docker", "compose", "images", "-q", "web"], text=True, timeout=10).strip()
    daemon = subprocess.check_output(prefix + ["info", "--format", "{{.ID}}"], text=True, timeout=10).strip()
    name = "hotel-ai-controls-fixture-" + uuid4().hex
    container_id = subprocess.check_output(prefix + [
        "create", "--name", name, "--network", "none", "--memory", "32m", "--cpus", "0.25",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--label", "hotel-ai.controller-owner=ci-fixture", "--entrypoint", "python", image,
        "-c", "import time; time.sleep(60)",
    ], text=True, timeout=10).strip()
    try:
        image_id = subprocess.check_output(prefix + ["inspect", "--format", "{{.Image}}", container_id],
                                           text=True, timeout=10).strip()
        approved = ApprovedContainer(name, container_id, image_id, 250000000, 32 * 1048576, "ci-fixture")
        controls = DockerControls(socket, daemon, [approved])
        for action, expected in ((controls.start, "running"), (controls.start, "running"),
                                 (controls.stop, "exited"), (controls.stop, "exited")):
            if action(name) != expected:
                raise RuntimeError("Unexpected fixture state.")
        print("Approved start/stop and idempotent checks passed on isolated fixture.")
    finally:
        subprocess.run(prefix + ["rm", "--force", container_id], check=True, timeout=10,
                       stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
