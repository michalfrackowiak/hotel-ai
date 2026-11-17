"""Run the complete speech runtime on a disposable hosted Linux CI runner."""
from contextlib import closing, contextmanager
from dataclasses import asdict
from http.client import HTTPConnection, HTTPException
from ipaddress import ip_network
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.admission import ControllerAdmission, ResourcePolicy
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.docker_control import ApprovedContainer, ControlUnavailable, DockerControls, private_ipv4
from speech_controller.endpoint_binding import BoundReadiness, EndpointBinding
from speech_controller.lifecycle import SwitchRequest
from speech_controller.journal import JournalUnavailable
from speech_controller.readiness import ProbeTarget
from speech_controller.resources import host_resources
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from speech_controller.traffic import TrafficUnavailable


GIB = 1024 ** 3
CPU = 500_000_000
MEMORY = {"tts": GIB, "stt": 3 * GIB // 2}
WEB_MEMORY = GIB // 2
OVERLAP = sum(MEMORY.values()) + max(MEMORY.values())
LANGUAGES = ("en", "pl")
OWNER_LABEL = "hotel-ai.controller-owner"
PENDING_REQUEST = "stage-interrupted"


class FixtureError(Exception):
    pass


class InterruptedSwitch(Exception):
    pass


def require(condition, message):
    if not condition:
        raise FixtureError(message)


def native_socket(environment):
    require(platform.system() == "Linux" and environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Speech runtime staging requires a disposable GitHub-hosted Linux runner.")
    path = Path("/var/run/docker.sock").resolve(strict=True)
    require(stat.S_ISSOCK(path.stat().st_mode), "Native Docker socket unavailable.")
    return str(path)


def full_id(value, *, image=False):
    require(bool(re.fullmatch(("sha256:" if image else "") + r"[a-f0-9]{64}", value)),
            "Docker returned an invalid full identity.")
    return value


class DockerFixture:
    def __init__(self, socket_path):
        self.prefix = ["docker", "--host", "unix://" + socket_path]
        self.env = {"PATH": os.environ.get("PATH", os.defpath)}
        self.owner = "hotel-ai-stage-" + uuid4().hex
        self.label = OWNER_LABEL + "=" + self.owner
        self.containers = set()
        self.networks = set()

    def run(self, *args, timeout=10, env=None, stream=False):
        try:
            completed = subprocess.run(self.prefix + list(args), check=True, timeout=timeout,
                env=self.env if env is None else env, text=True,
                stdout=None if stream else subprocess.PIPE, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise FixtureError(f"Docker fixture command failed ({args[0]}).") from None
        return completed.stdout.strip() if not stream else None

    def images(self):
        base = ("compose", "--env-file", "/dev/null", "-f", "compose.yaml", "-f", "compose.piper.yaml",
                "-f", "compose.asr.yaml", "images", "-q")
        images = {}
        for service in ("web", "piper", "asr"):
            candidate = self.run(*base, service).removeprefix("sha256:")
            require(bool(re.fullmatch(r"[a-f0-9]{12,64}", candidate)), "Expected one local Compose image identity.")
            image = full_id(self.run("image", "inspect", "--format", "{{.Id}}", candidate), image=True)
            require(image.removeprefix("sha256:").startswith(candidate), "Compose image identity changed.")
            images[service] = image
        return images

    def network(self):
        options = ("network", "create", "--driver", "bridge", "--internal", "--label", self.label)
        provisional = full_id(self.run(*options, self.owner + "-pool"))
        self.networks.add(provisional)
        ipam = json.loads(self.run("network", "inspect", "--format", "{{json .IPAM.Config}}", provisional))
        require(isinstance(ipam, list) and len(ipam) == 1, "Fixture requires one IPv4 subnet.")
        subnet = ip_network(ipam[0]["Subnet"], strict=True)
        require(subnet.version == 4 and subnet.num_addresses >= 16
                and private_ipv4(str(subnet.network_address)) and private_ipv4(str(subnet.broadcast_address)),
                "Docker pool did not allocate a private IPv4 fixture subnet.")
        self.run("network", "rm", provisional)
        self.networks.remove(provisional)
        network_id = full_id(self.run(*options, "--subnet", str(subnet), self.owner))
        self.networks.add(network_id)
        return network_id, [str(subnet.network_address + host) for host in range(4, 8)]

    def create(self, role, *args, env=None):
        identity = full_id(self.run("create", "--pull", "never", "--name", self.owner + "-" + role,
            "--label", self.label, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--init", "--cpus", "0.5", *args, env=env))
        self.containers.add(identity)
        return identity

    def owned_ids(self, kind):
        listing = ("ps", "--all") if kind == "container" else ("network", "ls")
        return [full_id(value) for value in self.run(*listing, "--no-trunc", "--filter", "label=" + self.label,
                                                    "--format", "{{.ID}}").splitlines()]

    def cleanup(self):
        # Discover only this run's unique label, including any create whose
        # response was lost. Remove only full identities returned by this daemon.
        failed = False
        targets = (("container", self.containers, ("rm", "--force")), ("network", self.networks, ("network", "rm")))
        for kind, known, remove in targets:
            try:
                known.update(self.owned_ids(kind))
            except FixtureError:
                failed = True
            for identity in sorted(known):
                try:
                    self.run(*remove, identity)
                except FixtureError:
                    failed = True
            try:
                failed |= bool(self.owned_ids(kind))
            except FixtureError:
                failed = True
        require(not failed, "Could not remove every staged Docker resource.")


def ready(url, *, token=None, timeout=20, process=None):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if process is not None and process.poll() is not None:
            break
        connection = HTTPConnection(*url, timeout=1)
        try:
            headers = {"Authorization": "Bearer " + token} if token else {}
            connection.request("GET", "/v1/status" if token else "/healthz", headers=headers)
            response = connection.getresponse()
            if response.status == 200:
                raw = response.read(65537)
                require(len(raw) <= 65536, "Staged response exceeded its limit.")
                return json.loads(raw) if token else None
        except (OSError, HTTPException):
            pass
        finally:
            connection.close()
        sleep(0.1)
    raise FixtureError("Staged controller or worker did not become ready.")


def make_stores(directory, controls, policy, targets, bindings, names):
    fixture_dir = directory / "django"
    fixture_dir.mkdir(mode=0o700)
    traffic_path = fixture_dir / "traffic.sqlite3"
    journal_path = directory / "controller.sqlite3"
    routes = {(language, task): names[task + "-old"] for language in LANGUAGES for task in MEMORY}
    initialize_routing(traffic_path, targets, routes)
    traffic = RoutedTrafficGate(traffic_path, targets)
    admission = ControllerAdmission(controls, policy)
    backend = DockerLifecycleBackend(controls, traffic.deployments, admission=admission, traffic=traffic,
        readiness=BoundReadiness(controls, targets, bindings), reconciliation=admission.verify_reconciliation)
    executor = RoutedLifecycleExecutor(backend, traffic.deployments, routes, journal_path)
    journal_path.chmod(0o600)
    # Seed one interrupted test request through the real journal. The forced
    # interruption precedes worker or route effects and is gone before startup.
    with patch.object(executor, "_run_pending", side_effect=InterruptedSwitch):
        try:
            executor.execute(SwitchRequest(PENDING_REQUEST, 0, ("en", "stt"), names["stt-new"]))
        except InterruptedSwitch:
            pass
        else:
            raise FixtureError("Could not seed the interrupted staging request.")
    return fixture_dir, traffic_path, journal_path


@contextmanager
def runtime(command, env):
    try:
        subprocess.run([*command, "--check"], check=True, timeout=30, env=env)
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise FixtureError("Native controller preflight failed.") from None
    try:
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise FixtureError("Native controller did not shut down cleanly.") from None


def stage(docker, socket_path, directory):
    images = docker.images()
    info = json.loads(docker.run("info", "--format", '{"id":{{json .ID}},"os":{{json .OSType}},'
                                '"system":{{json .OperatingSystem}},"storage":{{json .DockerRootDir}}}'))
    require(info["os"] == "linux" and "docker desktop" not in info["system"].lower()
            and Path(info["storage"]).is_absolute(), "Expected native Linux Docker storage.")
    resources = host_resources(info["storage"])
    require((resources["cpu_count"] or 0) >= 2 and (resources["memory_available_mib"] or 0) * 1024 ** 2
            >= OVERLAP + WEB_MEMORY + GIB, "Runner does not have enough reserved CPU and RAM for staging.")
    estimates = {images[service]: int(docker.run("image", "inspect", "--format", "{{.Size}}", images[service]))
                 for service in ("piper", "asr")}
    require((resources["disk_free_mib"] or 0) * 1024 ** 2 >= GIB + sum(estimates.values()),
            "Runner does not have enough reserved disk space for staging.")
    policy = ResourcePolicy(info["id"], info["storage"], True, 3 * CPU, OVERLAP, GIB, GIB, tuple(estimates.items()))

    network, addresses = docker.network()
    names, approvals, targets, bindings = {}, [], [], []
    roles = ("tts-old", "tts-new", "stt-old", "stt-new")
    for role, address in zip(roles, addresses, strict=True):
        task, _ = role.split("-")
        image = images["piper" if task == "tts" else "asr"]
        name = docker.owner + "-" + role
        memory = str(MEMORY[task])
        container = docker.create(role, "--network", network, "--ip", address, "--memory", memory,
                                  "--memory-swap", memory, image)
        names[role] = name
        approvals.append(ApprovedContainer(name, container, image, CPU, int(memory), docker.owner))
        targets.append(ProbeTarget(name, task, f"http://{address}:{5000 if task == 'tts' else 5001}", LANGUAGES))
        bindings.append(EndpointBinding(name, network, address))

    controls = DockerControls(socket_path, info["id"], approvals)
    for role, binding in zip(roles, bindings, strict=True):
        if role.endswith("-old"):
            controls.start(names[role])
            ready((binding.address, 5000 if role.startswith("tts") else 5001))
        else:
            require(controls.inspect(names[role]) == "created", "Replacement worker was already started.")
    fixture_dir, traffic_path, journal_path = make_stores(directory, controls, policy, targets, bindings, names)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    manifest = directory / "runtime.json"
    policy_json = asdict(policy)
    del policy_json["daemon_id"]
    manifest.touch(mode=0o600, exist_ok=False)
    manifest.write_text(json.dumps(dict(version=1, docker_socket=socket_path, daemon_id=controls.daemon_id,
        actor="runtime-ci", port=port, traffic_path=str(traffic_path), journal_path=str(journal_path), policy=policy_json,
        deployments=[asdict(approval) | asdict(target) | asdict(binding) for approval, target, binding in
                     zip(approvals, targets, bindings, strict=True)])), encoding="utf-8")
    command = [sys.executable, "-m", "speech_controller.runtime", "--config", str(manifest)]
    env = docker.env | {"HOTEL_SPEECH_CONTROL_TOKEN": token}
    endpoint = ("127.0.0.1", port)
    with runtime(command, env) as process:
        initial = ready(endpoint, token=token, process=process)
        require(initial["blocked"] is True and initial["state"]["revision"] == 0
                and initial["pending_requests"] == [PENDING_REQUEST], "Controller lost its pending switch on startup.")
        with closing(HTTPConnection(*endpoint, timeout=3)) as connection:
            connection.request("GET", "/v1/status")
            require(connection.getresponse().status == 401, "Controller exposed status without a token.")
        duplicate = subprocess.run([*command, "--check"], timeout=10, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(duplicate.returncode == 1, "Controller startup permitted a competing owner.")
        launch_admin(docker, images["web"], fixture_dir, targets, names, port, token)
        final = ready(endpoint, token=token, process=process)
        routes = dict(((language, task), name) for (language, task), name in final["state"]["routes"])
        require(final["blocked"] is False and not final["pending_requests"] and not final["pending_recoveries"]
                and final["state"] == final["observed"] and final["state"]["revision"] == 4
                and routes == {(language, task): names[task + "-new"] for language in LANGUAGES for task in MEMORY},
                "Final controller routes or recovery state did not match staging expectations.")
        for role in roles:
            expected = "exited" if role.endswith("-old") else "running"
            require(controls.inspect(names[role]) == expected, "Worker references and Docker state disagree.")
    with closing(sqlite3.connect(journal_path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        row = db.execute("SELECT outcome FROM operations WHERE request_id=?", (PENDING_REQUEST,)).fetchone()
        require(row is not None and json.loads(row[0])["status"] == "reconciled",
                "Expected a durable reconciliation result for the staged interruption.")


def launch_admin(docker, image, fixture_dir, targets, names, port, token):
    settings = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "DJANGO_SECRET_KEY": secrets.token_hex(32), "DJANGO_ALLOWED_HOSTS": "testserver",
        "DJANGO_DB_PATH": "/fixture/bookings.sqlite3", "HOTEL_SPEECH_TRAFFIC_DB": "/fixture/traffic.sqlite3",
        "HOTEL_SPEECH_ROUTE_TARGETS": json.dumps([asdict(target) for target in targets]),
        "HOTEL_SPEECH_CONTROL_URL": f"http://127.0.0.1:{port}", "HOTEL_SPEECH_CONTROL_TOKEN": token,
        "HOTEL_SPEECH_CONTROL_HOST": "runtime-ci", "HOTEL_SPEECH_CONTROL_SWITCH_ENABLED": "1",
        "HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED": "1", "HOTEL_STAGE_WORKERS": json.dumps(names),
    }
    env_args = [item for key in settings for item in ("--env", key)]
    web = docker.create("django", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
        "--memory", str(WEB_MEMORY), "--memory-swap", str(WEB_MEMORY), "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
        "--mount", f"type=bind,src={fixture_dir},dst=/fixture", *env_args, image,
        "python", "scripts/check_staged_speech_admin.py", env=docker.env | settings)
    docker.run("start", "--attach", web, timeout=150, stream=True)


def main():
    try:
        socket_path = native_socket(os.environ)
        with TemporaryDirectory(prefix="hotel-ai-runtime-") as directory:
            docker = DockerFixture(socket_path)
            try:
                stage(docker, socket_path, Path(directory).resolve())
            finally:
                docker.cleanup()
        print("Real runtime staging passed. Old workers stopped, replacements routed, and fixture removed.")
    except FixtureError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, HTTPException, subprocess.SubprocessError,
            ControlUnavailable, JournalUnavailable, TrafficUnavailable):
        raise SystemExit("Speech runtime staging configuration or state was invalid.") from None


if __name__ == "__main__":
    main()
