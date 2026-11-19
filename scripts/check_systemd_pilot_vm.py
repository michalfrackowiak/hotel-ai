"""Install and check the routed service units inside a dedicated fictional QEMU guest."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
from time import monotonic, sleep


SOURCE = Path(__file__).resolve().parents[1]
IDENTITY = "hotel-ai-systemd-rehearsal"
ROOT = Path("/var/lib/hotel-ai-speech")
STATE = ROOT / "state"
OPERATOR = ROOT / "operator"
RELEASE = Path("/opt/hotel-ai-speech/boot")
UNITS = Path("/etc/systemd/system")
CONTROLLER = "hotel-ai-speech-pilot.service"
WEB = "hotel-ai-speech-web.service"
SOCKET = "/run/docker.sock"
WEB_ID = "1" * 64
IMAGE = "sha256:" + "2" * 64
DAEMON = "fixture-systemd-daemon"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
EVENTS = STATE / "events"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def require_fixture():
    marker = Path("/etc") / IDENTITY
    info = marker.lstat()
    require(os.geteuid() == 0 and stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600 and marker.read_text().strip() == IDENTITY
            and Path("/sys/class/dmi/id/product_name").read_text().strip() == IDENTITY
            and Path("/proc/1/comm").read_text().strip() == "systemd",
            "Run this command as root inside the marked disposable systemd VM.")


def write(path, value, mode=0o600):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode), "w") as file:
        file.write(value)


def command(*arguments, check=True):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=195, check=check)


def property_of(unit, name):
    return command("systemctl", "show", "--property=" + name, "--value", unit).stdout.strip()


def until(predicate, message, timeout=30):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.15)
    raise RuntimeError(message)


def events_for(boot_id):
    return [event for line in EVENTS.read_text().splitlines()
            for recorded_boot, event in [line.split(" ", 1)] if recorded_boot == boot_id]


def assert_started_in_order(boot_id):
    until(lambda: "web-started" in events_for(boot_id), "The ordered boot did not launch web.")
    events = events_for(boot_id)
    require(events.count("web-started") == 1 and "authenticated-ready" in events
            and events.index("authenticated-ready") < events.index("web-started")
            and "authenticated-blocked" not in events,
            "Web did not start once after authenticated clean readiness.")
    require(property_of(CONTROLLER, "ActiveState") == "active" and property_of(WEB, "ActiveState") == "active",
            "The controller and web did not stay active.")


def install_fixture():
    docker = Path("/usr/bin/docker")
    destinations = (ROOT, RELEASE.parent, docker, Path(SOCKET),
                    *(UNITS / name for name in ("docker.service", WEB, CONTROLLER)))
    require(not any(os.path.lexists(path) for path in destinations),
            "Fixture paths must be absent. Use an untouched disposable guest.")
    command("groupadd", "--system", "docker")
    command("groupadd", "--gid", "10001", "hotel-ai-speech")
    command("useradd", "--uid", "10001", "--gid", "10001", "--no-create-home",
            "--shell", "/usr/sbin/nologin", "hotel-ai-speech")
    traffic_dir = ROOT / "traffic"
    ROOT.mkdir(mode=0o755)
    for directory in (STATE, OPERATOR, traffic_dir):
        directory.mkdir(mode=0o700)
    for directory in (STATE, traffic_dir):
        os.chown(directory, 10001, 10001)

    RELEASE.mkdir(mode=0o755, parents=True)
    shutil.copytree(SOURCE / "speech_controller", RELEASE / "speech_controller")
    (RELEASE / "model_providers").mkdir(mode=0o755)
    shutil.copyfile(SOURCE / "model_providers/control_status_worker.py",
                    RELEASE / "model_providers/control_status_worker.py")
    (RELEASE / "speech_controller/runtime.py").write_text(RUNTIME, encoding="utf-8")
    write(docker, DOCKER_STUB, 0o755)
    write(UNITS / "docker.service", DOCKER_UNIT, 0o644)
    for name in (CONTROLLER, WEB):
        write(UNITS / name, (SOURCE / "deploy/systemd" / name).read_text(), 0o644)

    sys.path.insert(0, str(SOURCE))
    from speech_controller.readiness import ProbeTarget
    from speech_controller.routing import initialize_routing

    targets = (
        ProbeTarget("fixture-tts", "tts", "http://10.70.0.2:5000", ("en", "pl")),
        ProbeTarget("fixture-stt", "stt", "http://10.70.0.3:5001", ("en", "pl")),
    )
    traffic = traffic_dir / "traffic.sqlite3"
    initialize_routing(traffic, targets, {(language, target.task): target.name
                                          for target in targets for language in target.languages})
    journal = STATE / "journal.sqlite3"
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY)")
    journal.chmod(0o600)
    deployments = [
        asdict(target) | {"container_id": str(index) * 64, "image_id": IMAGE, "nano_cpus": 200000000,
                          "memory_bytes": 67108864, "owner": "fictional", "network_id": "9" * 64,
                          "address": f"10.70.0.{index}"}
        for index, target in enumerate(targets, start=2)
    ]
    manifest = STATE / "controller.json"
    write(manifest, json.dumps({
        "version": 1, "actor": "systemd-fixture", "port": 8093, "docker_socket": SOCKET,
        "daemon_id": DAEMON, "traffic_path": str(traffic), "journal_path": str(journal),
        "policy": {"storage_path": str(ROOT), "topology_confirmed": True, "cpu_budget_nanos": 1000000000,
                   "memory_budget_bytes": 268435456, "memory_reserve_bytes": 67108864,
                   "disk_reserve_bytes": 67108864, "artifact_bytes": [[IMAGE, 1048576]]},
        "deployments": deployments,
    }))
    write(EVENTS, "")
    for path in (manifest, traffic, journal, EVENTS):
        os.chown(path, 10001, 10001)
    write(OPERATOR / "controller.env", f"HOTEL_SPEECH_CONTROL_TOKEN={secrets.token_hex(32)}\n")
    write(OPERATOR / "web.json", json.dumps({
        "daemon_id": DAEMON, "container_id": WEB_ID, "image_id": IMAGE,
        "booking_volume": "hotel-ai-rehearsal", "traffic_dir": str(traffic_dir),
    }))
    write(OPERATOR / "initial-boot", BOOT_ID.read_text())
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "--now", WEB)
    assert_started_in_order(BOOT_ID.read_text().strip())
    print("Initial systemd startup: authenticated readiness preceded the web start.")


def check_after_reboot():
    initial_boot = (OPERATOR / "initial-boot").read_text().strip()
    current_boot = BOOT_ID.read_text().strip()
    require(current_boot != initial_boot, "The disposable VM did not reboot.")
    previous = events_for(initial_boot)
    require("web-stopped" in previous and "controller-stopped" in previous
            and previous.index("web-stopped") < previous.index("controller-stopped"),
            "The previous boot did not stop web before its controller.")
    assert_started_in_order(current_boot)
    print("VM boot ID changed. Shutdown stopped web first and reboot waited for authenticated readiness.")

    pid = int(property_of(CONTROLLER, "MainPID"))
    require(pid > 1 and property_of(CONTROLLER, "ActiveState") == "active", "Fixture controller is unavailable.")
    os.kill(pid, signal.SIGKILL)
    until(lambda: property_of(WEB, "ActiveState") == "inactive", "Web stayed active after controller loss.")
    require(events_for(current_boot).count("web-stopped") == 1, "Controller loss did not stop the web container.")
    print("Unexpected controller exit stopped the bound web service.")

    write(STATE / "blocked", "fixture blocked controller status\n")
    command("systemctl", "reset-failed", CONTROLLER, WEB)
    start = command("systemctl", "start", WEB, check=False)
    require(start.returncode != 0 and property_of(CONTROLLER, "ActiveState") == "failed"
            and property_of(WEB, "ActiveState") == "inactive", "Blocked readiness did not prevent systemd startup.")
    after = events_for(current_boot)
    require(after.count("web-started") == 1 and "authenticated-blocked" in after,
            "Blocked readiness did not hold the web container stopped.")
    print("Authenticated blocked controller status failed startup with web stopped.")


EVENT_FUNCTION = """
from pathlib import Path

def event(label):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    with Path("/var/lib/hotel-ai-speech/state/events").open("a") as stream:
        stream.write(f"{boot} {label}\\n")
"""

DOCKER_UNIT = """[Unit]
Description=Fictional deployment fixture

[Service]
Type=notify
ExecStart=/usr/bin/docker fixture-daemon
"""

DOCKER_STUB = """#!/usr/bin/python3
import grp
import json
import os
import socket
import sys
from time import sleep
""" + EVENT_FUNCTION + f"""

web_id = {WEB_ID!r}
running = Path("/run/hotel-ai-fictional-web")
args = sys.argv[1:]
if args == ["fixture-daemon"]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind({SOCKET!r})
        os.chown({SOCKET!r}, 0, grp.getgrnam("docker").gr_gid)
        os.chmod({SOCKET!r}, 0o660)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.sendto(b"READY=1", os.environ["NOTIFY_SOCKET"].replace("@", "\\0", 1))
        while True:
            sleep(1)
if args[:2] != ["--host", "unix://{SOCKET}"]:
    sys.exit(1)
args = args[2:]
if args[:2] == ["info", "--format"] and len(args) == 3:
    print(json.dumps({{"id": {DAEMON!r}, "os_type": "linux", "live_restore": False}}))
elif args[:4] == ["inspect", "--type", "container", "--format"] and args[5:] == [web_id]:
    print(json.dumps({{
        "id": web_id, "image": {IMAGE!r}, "state": "running" if running.exists() else "exited",
        "restart": "no", "network": "host", "bindings": None, "publish_all": False,
        "privileged": False, "user": "10001:10001",
        "mounts": [
            {{"type": "volume", "name": "hotel-ai-rehearsal", "source": "/fictional-volume",
              "destination": "/app/data", "rw": True}},
            {{"type": "bind", "name": None, "source": "/var/lib/hotel-ai-speech/traffic",
              "destination": "/run/hotel-ai-traffic", "rw": True}},
        ]
    }}))
elif args == ["ps", "--quiet", "--filter", "volume=hotel-ai-rehearsal"]:
    if running.exists():
        print(web_id)
elif args == ["start", "--attach", web_id]:
    running.touch(exist_ok=False)
    event("web-started")
    while running.exists():
        sleep(0.05)
elif args == ["stop", "--time", "30", web_id] and running.exists():
    event("web-stopped")
    running.unlink()
else:
    sys.exit(1)
"""

RUNTIME = """
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import os
from time import sleep

from .routing import RoutedTrafficGate
from .runtime_config import RuntimeConfiguration
""" + EVENT_FUNCTION + """

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--boot-workers", action="store_true", required=True)
args = parser.parse_args()
token = os.environ["HOTEL_SPEECH_CONTROL_TOKEN"]
config = RuntimeConfiguration.load(args.config, token)
state = asdict(RoutedTrafficGate(config.traffic_path, config.targets).state)
blocked = Path("/var/lib/hotel-ai-speech/state/blocked").exists()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        actual = self.headers.get("Authorization", "")
        if self.path != "/v1/status" or not hmac.compare_digest(actual, "Bearer " + token):
            self.send_error(401)
            return
        event("authenticated-blocked" if blocked else "authenticated-ready")
        payload = json.dumps({"blocked": blocked, "state": state, "observed": state,
                              "pending_requests": [], "pending_recoveries": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


sleep(0.8)
try:
    with HTTPServer(("127.0.0.1", config.control.port), Handler) as server:
        server.serve_forever()
except KeyboardInterrupt:
    event("controller-stopped")
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "after-reboot"))
    arguments = parser.parse_args()
    require_fixture()
    if arguments.phase == "prepare":
        install_fixture()
    else:
        check_after_reboot()
