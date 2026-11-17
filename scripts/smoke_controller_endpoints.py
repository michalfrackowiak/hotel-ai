"""Linux CI: verify an approved container's private bridge address, without ports."""
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.docker_control import ApprovedContainer, ControlUnavailable, DockerControls


def main():
    socket = "/var/run/docker.sock"
    prefix = ["docker", "--host", "unix://" + socket]

    def docker(*args):
        return subprocess.check_output(prefix + list(args), text=True, timeout=10).strip()

    image = subprocess.check_output(["docker", "compose", "images", "-q", "web"], text=True, timeout=10).strip()
    daemon = docker("info", "--format", "{{.ID}}")
    name = "hotel-ai-endpoint-fixture-" + uuid4().hex
    network_id = docker("network", "create", "--internal", "--driver", "bridge", name)
    container_id = None
    try:
        container_id = docker("create", "--name", name, "--network", network_id,
            "--memory", "32m", "--cpus", "0.25", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--label", "hotel-ai.controller-owner=ci-endpoint",
            "--entrypoint", "python", image, "-c", "import time; time.sleep(120)")
        image_id = docker("inspect", "--format", "{{.Image}}", container_id)
        approval = ApprovedContainer(name, container_id, image_id, 250000000, 32 * 1048576, "ci-endpoint")
        controls = DockerControls(socket, daemon, [approval])
        controls.start(name)
        networks = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", container_id))
        address = networks[name]["IPAddress"]
        if controls.verify_endpoint(name, network_id, address) is not True:
            raise RuntimeError("Approved fixture endpoint was not verified.")
        wrong_address = "10.255.255.254" if address != "10.255.255.254" else "10.255.255.253"
        for wrong_network, wrong_address in (("0" * 64, address), (network_id, wrong_address)):
            try:
                controls.verify_endpoint(name, wrong_network, wrong_address)
            except (ControlUnavailable, ValueError):
                pass
            else:
                raise RuntimeError("Unapproved fixture endpoint was accepted.")
        controls.stop(name)
        try:
            controls.verify_endpoint(name, network_id, address)
        except ControlUnavailable:
            pass
        else:
            raise RuntimeError("Stopped fixture endpoint was accepted.")
        print("Approved private bridge endpoint verified; wrong network/address and stopped worker rejected.")
    finally:
        try:
            if container_id:
                subprocess.run(prefix + ["rm", "--force", container_id], check=True, timeout=10,
                               stdout=subprocess.DEVNULL)
        finally:
            subprocess.run(prefix + ["network", "rm", network_id], check=True, timeout=10,
                           stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
