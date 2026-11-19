"""Systemd helpers for the native routed speech pilot."""
import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from time import monotonic, sleep

from .docker_control import DAEMON_ID, IDENTIFIER, unique_keys
from .routing import RoutedTrafficGate
from .runtime_config import RuntimeConfiguration


STATUS_WORKER = Path(__file__).resolve().parents[1] / "model_providers" / "control_status_worker.py"
DOCKER_SOCKET = Path("/run/docker.sock")
DOCKER = ("/usr/bin/docker", "--host", f"unix://{DOCKER_SOCKET}")
DOCKER_ENV = {"PATH": "/usr/bin:/bin"}
WEB_OWNER = Path("/run/hotel-ai-speech-web/attached")
DAEMON_FORMAT = '{"id":{{json .ID}},"os_type":{{json .OSType}},"live_restore":{{json .LiveRestoreEnabled}}}'
WEB_FORMAT = (
    '{"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State.Status}},'
    '"restart":{{json .HostConfig.RestartPolicy.Name}},"network":{{json .HostConfig.NetworkMode}},'
    '"bindings":{{json .HostConfig.PortBindings}},"publish_all":{{json .HostConfig.PublishAllPorts}},'
    '"privileged":{{json .HostConfig.Privileged}},"user":{{json .Config.User}},'
    '"mounts":[{{range $i, $m := .Mounts}}{{if $i}},{{end}}'
    '{"type":{{json $m.Type}},"name":{{if eq $m.Type "volume"}}{{json $m.Name}}{{else}}null{{end}},'
    '"source":{{json $m.Source}},"destination":{{json $m.Destination}},"rw":{{json $m.RW}}}{{end}}]}'
)


def ready_status(value, expected):
    return (type(value) is dict and set(value) == {
                "blocked", "state", "observed", "pending_requests", "pending_recoveries"}
            and value["blocked"] is False and value["pending_requests"] == []
            and value["pending_recoveries"] == []
            # Canonical JSON preserves distinctions such as true and 1 in observed state.
            and json.dumps(value["state"], sort_keys=True) == expected
            and json.dumps(value["observed"], sort_keys=True) == expected)


def wait_controller(config_path, token):
    config = RuntimeConfiguration.load(config_path, token)
    request = json.dumps({"url": f"http://127.0.0.1:{config.control.port}/v1/status",
                          "token": config.control.token}).encode()
    deadline = monotonic() + 120
    while (remaining := deadline - monotonic()) > 0:
        try:
            response = subprocess.run([sys.executable, "-I", str(STATUS_WORKER)], input=request,
                                      capture_output=True, timeout=min(6, remaining), check=True)
        except subprocess.SubprocessError:
            sleep(min(0.25, max(0, deadline - monotonic())))
            continue
        value = json.loads(response.stdout, object_pairs_hook=unique_keys)
        traffic = RoutedTrafficGate(config.traffic_path, config.targets)
        expected = json.dumps(asdict(traffic.state), sort_keys=True)
        if ready_status(value, expected) and monotonic() < deadline:
            print("Authenticated controller readiness verified.")
            return
        raise ValueError("Controller status disagrees with the approved saved routes.")
    raise TimeoutError("Controller readiness deadline expired.")


@dataclass(frozen=True)
class WebApproval:
    daemon_id: str
    container_id: str
    image_id: str
    booking_volume: str
    traffic_dir: str

    @classmethod
    def load(cls, value):
        path = Path(value)
        if (os.geteuid() != 0 or not path.is_absolute() or str(path.resolve(strict=True)) != value
                or any(part.is_symlink() for part in (path, *path.parents))):
            raise ValueError("Web approval requires a canonical root-owned path.")
        for parent in path.parents:
            info = parent.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Web approval directories must be root-owned and protected.")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
                    or info.st_nlink != 1 or not 0 < info.st_size <= 4096):
                raise ValueError("Expected a private root-owned web approval file.")
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("Web approval is too large.")
        approval = cls(**json.loads(raw, object_pairs_hook=unique_keys))
        mount = Path(approval.traffic_dir)
        if (not DAEMON_ID.fullmatch(approval.daemon_id)
                or not re.fullmatch(r"[a-f0-9]{64}", approval.container_id)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", approval.image_id)
                or not IDENTIFIER.fullmatch(approval.booking_volume)
                or not mount.is_absolute() or str(mount.resolve(strict=True)) != approval.traffic_dir
                or not mount.is_dir() or any(ord(c) < 32 for c in approval.traffic_dir)):
            raise ValueError("Invalid web approval.")
        return approval


def docker_output(*args):
    return subprocess.run([*DOCKER, *args], env=DOCKER_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=5, check=True).stdout


def web_state(approval):
    if not stat.S_ISSOCK(DOCKER_SOCKET.lstat().st_mode):
        raise ValueError("Expected a local Docker socket.")
    daemon = json.loads(docker_output("info", "--format", DAEMON_FORMAT))
    if daemon != {"id": approval.daemon_id, "os_type": "linux", "live_restore": False}:
        raise ValueError("Expected the pinned Linux Docker daemon with live restore disabled.")
    state = json.loads(docker_output("inspect", "--type", "container", "--format", WEB_FORMAT,
                                     approval.container_id), object_pairs_hook=unique_keys)
    if state["id"] != approval.container_id or state["image"] != approval.image_id:
        raise ValueError("The web container does not match the approval.")
    return state


def check_web_offline(approval, state):
    actual_mounts = list(state["mounts"])
    if (state["state"] not in ("created", "exited") or state["restart"] != "no"
            or state["network"] != "host" or state["bindings"] not in ({}, None)
            or state["publish_all"] is not False or state["privileged"] is not False
            or state["user"] != "10001:10001" or len(actual_mounts) != 2):
        raise ValueError("Web container must be offline under the systemd deployment profile.")
    by_destination = {item["destination"]: item for item in actual_mounts}
    booking = by_destination["/app/data"]
    traffic = by_destination["/run/hotel-ai-traffic"]
    if (booking["type"] != "volume" or booking["name"] != approval.booking_volume or booking["rw"] is not True
            or traffic["type"] != "bind" or traffic["source"] != approval.traffic_dir or traffic["rw"] is not True):
        raise ValueError("Web mounts disagree with the approved writable stores.")
    if docker_output("ps", "--quiet", "--filter", f"volume={approval.booking_volume}").strip():
        raise ValueError("A container already has the booking volume open.")


def run_web(action, approval_path):
    approval = WebApproval.load(approval_path)
    if action == "web-stop" and not WEB_OWNER.exists():
        return
    state = web_state(approval)
    if action == "web-stop":
        if WEB_OWNER.read_text(encoding="ascii") != approval.container_id:
            raise ValueError("The running web approval changed.")
        if state["state"] not in ("created", "exited"):
            subprocess.run([*DOCKER, "stop", "--time", "30", approval.container_id], env=DOCKER_ENV,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=40, check=True)
        WEB_OWNER.unlink()
        return
    check_web_offline(approval, state)
    if action == "web-run":
        with WEB_OWNER.open("x", encoding="ascii") as marker:
            marker.write(approval.container_id)
        os.execve(DOCKER[0], [*DOCKER, "start", "--attach", approval.container_id], DOCKER_ENV)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("wait-controller", "web-check", "web-run", "web-stop"))
    parser.add_argument("config")
    args = parser.parse_args(argv)
    try:
        if args.action == "wait-controller":
            wait_controller(args.config, os.environ.get("HOTEL_SPEECH_CONTROL_TOKEN", ""))
        else:
            run_web(args.action, args.config)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit("Pilot service check failed. Inspect private configuration and local service state.") from None


if __name__ == "__main__":
    main()
