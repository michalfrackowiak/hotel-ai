"""Run the speech runtime in a disposable Linux fixture."""
import argparse
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

from arm64_stage import (
    FixtureError, ProductionHealth, arm64_source, cleanup_on_disconnect, docker_path, production_snapshot, require,
)
from verify_recovery import database_checks, digest

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
MEMORY = {"tts": GIB, "stt": 3 * GIB // 2}
WEB_MEMORY = GIB // 2
OVERLAP = sum(MEMORY.values()) + max(MEMORY.values())
LANGUAGES = ("en", "pl")
OWNER_LABEL = "hotel-ai.controller-owner"
PENDING_REQUEST = "stage-interrupted"


class InterruptedSwitch(Exception):
    pass


def native_socket(environment, *, server=False):
    require(platform.system() == "Linux" and (server or (environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("RUNNER_ENVIRONMENT") == "github-hosted")),
            "Speech runtime staging requires a disposable GitHub-hosted Linux runner.")
    path = Path("/var/run/docker.sock").resolve(strict=True)
    require(stat.S_ISSOCK(path.stat().st_mode), "Native Docker socket unavailable.")
    return str(path)


def full_id(value, *, image=False):
    require(bool(re.fullmatch(("sha256:" if image else "") + r"[a-f0-9]{64}", value)),
            "Docker returned an invalid full identity.")
    return value


class DockerFixture:
    def __init__(self, socket_path, *, source=None, path=None):
        self.prefix = ["docker", "--host", "unix://" + socket_path]
        self.env = {"PATH": path if path is not None else os.environ.get("PATH", os.defpath)}
        self.source = source
        self.health = ProductionHealth() if source else None
        self.worker_cpu = 400_000_000 if source else 500_000_000
        self.web_cpu = "0.25" if source else "0.5"
        self.reserve = 3 * GIB if source else GIB
        self.daemon_id = None
        self.owner = "hotel-ai-stage-" + uuid4().hex
        self.label = OWNER_LABEL + "=" + self.owner
        self.containers = set()
        self.networks = set()

    def check_health(self):
        if self.health is not None:
            self.health.check()

    def run(self, *args, timeout=10, env=None, stream=False):
        if self.daemon_id is not None and (args[0] in ("create", "rm", "start")
                or args[:2] in (("network", "create"), ("network", "rm"))):
            self.check_daemon()
        try:
            completed = subprocess.run(self.prefix + list(args), check=True, timeout=timeout,
                env=self.env if env is None else env, text=True,
                stdout=None if stream else subprocess.PIPE, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise FixtureError(f"Docker fixture command failed ({args[0]}).") from None
        return completed.stdout.strip() if not stream else None

    def check_daemon(self):
        require(self.run("info", "--format", "{{.ID}}") == self.daemon_id,
                "The pinned Docker daemon changed during ARM64 staging.")

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
        self.check_health()
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
        self.check_health()
        network_id = full_id(self.run(*options, "--subnet", str(subnet), self.owner))
        self.networks.add(network_id)
        return network_id, [str(subnet.network_address + host) for host in range(4, 8)]

    def create(self, role, *args, env=None):
        self.check_health()
        cpu = self.web_cpu if role in ("django", "rollback") else str(self.worker_cpu / 1_000_000_000)
        identity = full_id(self.run("create", "--pull", "never", "--name", self.owner + "-" + role,
            "--label", self.label, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--init", "--cpus", cpu, *args, env=env))
        self.containers.add(identity)
        return identity

    def attach_admin(self, identity):
        self.check_health()
        if self.health is None:
            self.run("start", "--attach", identity, timeout=150, stream=True)
            return
        self.check_daemon()
        with subprocess.Popen(self.prefix + ["start", "--attach", identity],
                              env=self.env, stderr=subprocess.DEVNULL) as process:
            deadline = monotonic() + 150
            success = False
            try:
                while process.poll() is None:
                    self.check_health()
                    require(monotonic() < deadline, "Staged Django timed out.")
                    self.health.abort.wait(0.2)
                self.check_health()
                require(process.returncode == 0, "Staged Django returned an error.")
                success = True
            finally:
                if not success:
                    try:
                        self.run("rm", "--force", identity)
                        self.containers.remove(identity)
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=5)

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


def ready(url, *, token=None, timeout=20, process=None, check_health=lambda: None):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        check_health()
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


def stage(docker, socket_path, directory, *, production=None):
    images = docker.images() if production is None else {name: value["image"] for name, value in production.items()}
    info = json.loads(docker.run("info", "--format", '{"id":{{json .ID}},"os":{{json .OSType}},'
                                '"system":{{json .OperatingSystem}},"storage":{{json .DockerRootDir}}}'))
    require(info["os"] == "linux" and "docker desktop" not in info["system"].lower()
            and Path(info["storage"]).is_absolute(), "Expected native Linux Docker storage.")
    if production is not None:
        require(info["id"] == docker.daemon_id, "The pinned Docker daemon changed before ARM64 staging.")
        for image in images.values():
            require(docker.run("image", "inspect", "--format", "{{.Architecture}}", image) == "arm64",
                    "Expected cached ARM64 staging images.")
    resources = host_resources(info["storage"])
    require((resources["cpu_count"] or 0) >= 2 and (resources["memory_available_mib"] or 0) * 1024 ** 2
            >= OVERLAP + WEB_MEMORY + docker.reserve, "Host does not have enough reserved CPU and RAM for staging.")
    estimates = {images[service]: int(docker.run("image", "inspect", "--format", "{{.Size}}", images[service]))
                 for service in ("piper", "asr")}
    require((resources["disk_free_mib"] or 0) * 1024 ** 2 >= docker.reserve + sum(estimates.values()),
            "Host does not have enough reserved disk space for staging.")
    policy = ResourcePolicy(info["id"], info["storage"], True, 3 * docker.worker_cpu, OVERLAP,
                            docker.reserve, docker.reserve, tuple(estimates.items()))
    if production is not None:
        print("ARM64 capacity " + json.dumps({"available_mib": round(resources["memory_available_mib"], 1),
              "disk_free_mib": round(resources["disk_free_mib"], 1), "logical_cpus": resources["cpu_count"],
              "image_ids": images}, sort_keys=True), flush=True)

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
        approvals.append(ApprovedContainer(name, container, image, docker.worker_cpu, int(memory), docker.owner))
        targets.append(ProbeTarget(name, task, f"http://{address}:{5000 if task == 'tts' else 5001}", LANGUAGES))
        bindings.append(EndpointBinding(name, network, address))

    controls = DockerControls(socket_path, info["id"], approvals)
    for role, binding in zip(roles, bindings, strict=True):
        if role.endswith("-old"):
            docker.check_health()
            controls.start(names[role])
            ready((binding.address, 5000 if role.startswith("tts") else 5001), check_health=docker.check_health)
        else:
            require(controls.inspect(names[role]) == "created", "Replacement worker was already started.")
    fixture_dir, traffic_path, journal_path = make_stores(directory, controls, policy, targets, bindings, names)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    django_secret = secrets.token_hex(32)
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
    docker.check_health()
    with runtime(command, env) as process:
        initial = ready(endpoint, token=token, process=process, check_health=docker.check_health)
        require(initial["blocked"] is True and initial["state"]["revision"] == 0
                and initial["pending_requests"] == [PENDING_REQUEST], "Controller lost its pending switch on startup.")
        with closing(HTTPConnection(*endpoint, timeout=3)) as connection:
            connection.request("GET", "/v1/status")
            require(connection.getresponse().status == 401, "Controller exposed status without a token.")
        duplicate = subprocess.run([*command, "--check"], timeout=10, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(duplicate.returncode == 1, "Controller startup permitted a competing owner.")
        launch_admin(docker, images["web"], fixture_dir, targets, names, port, token, django_secret)
        final = ready(endpoint, token=token, process=process, check_health=docker.check_health)
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
    if production is None:
        require(process.returncode == 0, "The staged controller did not shut down normally.")
        rehearse_rollback(docker, images["web"], directory, fixture_dir, traffic_path, journal_path,
                          controls, names, {binding.name: binding for binding in bindings}, django_secret)


def launch_admin(docker, image, fixture_dir, targets, names, port, token, django_secret):
    settings = {
        "DJANGO_SECRET_KEY": django_secret, "DJANGO_ALLOWED_HOSTS": "testserver",
        "DJANGO_DB_PATH": "/fixture/bookings.sqlite3", "HOTEL_SPEECH_TRAFFIC_DB": "/fixture/traffic.sqlite3",
        "HOTEL_SPEECH_ROUTE_TARGETS": json.dumps([asdict(target) for target in targets]),
        "HOTEL_SPEECH_CONTROL_URL": f"http://127.0.0.1:{port}", "HOTEL_SPEECH_CONTROL_TOKEN": token,
        "HOTEL_SPEECH_CONTROL_HOST": "runtime-ci", "HOTEL_SPEECH_CONTROL_SWITCH_ENABLED": "1",
        "HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED": "1", "HOTEL_STAGE_WORKERS": json.dumps(names),
    }
    if docker.source:
        settings["HOTEL_STAGE_MODE"] = "arm64-server"
        env_file = fixture_dir.parent / "admin.env"
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in settings.items())
        options = ["--env-file", str(env_file), "--workdir", "/stage-src", "--mount",
                   f"type=bind,src={docker.source},dst=/stage-src,readonly"]
        env = docker.env
    else:
        settings.update(GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted")
        options = [item for key in settings for item in ("--env", key)]
        env = docker.env | settings
    web = docker.create("django", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
        "--memory", str(WEB_MEMORY), "--memory-swap", str(WEB_MEMORY), "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
        "--mount", f"type=bind,src={fixture_dir},dst=/fixture", *options, image,
        "python", "scripts/check_staged_speech_admin.py", env=env)
    docker.attach_admin(web)


def snapshot_sqlite(source, destination):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    os.close(descriptor)
    with (closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as original,
          closing(sqlite3.connect(destination, timeout=1)) as copy):
        original.backup(copy)


def rehearse_rollback(docker, image, directory, fixture_dir, traffic_path, journal_path,
                     controls, names, bindings, django_secret):
    # The stage has no remaining Django caller or controller. Preserve the
    # original routed stores and archive their bytes for offline inspection.
    with closing(sqlite3.connect(traffic_path.as_uri() + "?mode=ro", uri=True, timeout=1)) as traffic:
        require(traffic.execute("SELECT 1 FROM claims LIMIT 1").fetchone() is None,
                "Refusing worker rollback while staged speech claims remain.")
    archive = directory / "backup"
    archive.mkdir(mode=0o700)
    booking_path = fixture_dir / "bookings.sqlite3"
    originals = {path: digest(path) for path in (booking_path, traffic_path, journal_path)}
    archives = {}
    for source in (traffic_path, journal_path):
        target = archive / source.name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as copy:
            copy.write(source.read_bytes())
        require(digest(target) == originals[source], "Controller store archive differs from its source.")
        archives[target] = originals[source]
    backup = archive / booking_path.name
    restore_dir = directory / "rollback"
    restore_dir.mkdir(mode=0o700)
    restored = restore_dir / "restored.sqlite3"
    snapshot_sqlite(booking_path, backup)
    snapshot_sqlite(backup, restored)
    integrity, foreign_keys, counts = database_checks(booking_path)
    require(integrity and foreign_keys and counts and all(
        database_checks(path) == (True, True, counts) for path in (backup, restored)),
        "Booking snapshot or restored fixture failed SQLite verification.")
    backup_digest = digest(backup)
    print("Fixture SQLite backup and restore passed integrity, foreign-key and row-count checks.", flush=True)

    for task in MEMORY:
        controls.stop(names[task + "-new"])
        require(controls.inspect(names[task + "-new"]) == "exited", "Replacement worker did not stop for rollback.")
    settings = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "DJANGO_ALLOWED_HOSTS": "testserver",
        "DJANGO_SECRET_KEY": django_secret, "DJANGO_DB_PATH": "/fixture/restored.sqlite3",
    }
    for task, setting, port in (("tts", "HOTEL_PIPER_URL", 5000), ("stt", "HOTEL_ASR_URL", 5001)):
        name = names[task + "-old"]
        binding = bindings[name]
        controls.start(name)
        ready((binding.address, port))
        require(controls.verify_endpoint(name, binding.network_id, binding.address) is True,
                "Original worker endpoint binding differs after rollback.")
        settings[setting] = f"http://{binding.address}:{port}"
    options = [item for key in settings for item in ("--env", key)]
    rollback = docker.create("rollback", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
        "--memory", str(WEB_MEMORY), "--memory-swap", str(WEB_MEMORY), "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
        "--mount", f"type=bind,src={restore_dir},dst=/fixture", *options, image,
        "python", "scripts/check_staged_speech_rollback.py", env=docker.env | settings)
    docker.run("start", "--attach", rollback, timeout=45, stream=True)
    require(all(digest(path) == value for path, value in (originals | archives | {backup: backup_digest}).items()),
            "Rollback changed a source store or the booking backup.")
    print("Fixed speech rollback preserved the original booking, route and journal stores.", flush=True)


def stage_server(docker, socket_path, directory):
    identity = docker.run("info", "--format", "{{.ID}}")
    require(bool(re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", identity)), "Expected a native Docker daemon identity.")
    docker.daemon_id = identity
    before = production_snapshot(docker)
    try:
        with docker.health.monitor():
            try:
                stage(docker, socket_path, directory, production=before)
            finally:
                try:
                    docker.cleanup()
                finally:
                    require(docker.run("info", "--format", "{{.ID}}") == identity
                            and production_snapshot(docker) == before,
                            "Production daemon or service identity, start time, or health changed.")
                    require(docker.health.sample(), "Production health failed after staging cleanup.")
    finally:
        docker.health.report()
    docker.check_health()
    print("Production container identity, start time, and health matched before and after staging.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm64-server", action="store_true")
    parser.add_argument("--source")
    args = parser.parse_args(argv)
    try:
        require(args.arm64_server == (args.source is not None), "ARM64 server mode requires --source.")
        source = arm64_source(args.source, Path(__file__).resolve().parents[1]) if args.arm64_server else None
        socket_path = native_socket(os.environ, server=source is not None)
        with TemporaryDirectory(prefix="hotel-ai-runtime-", dir="/tmp" if source else None) as directory:
            directory = Path(directory).resolve()
            if source is not None:
                path = docker_path(directory)
                with cleanup_on_disconnect(), patch.dict(os.environ, {"PATH": path}):
                    stage_server(DockerFixture(socket_path, source=source, path=path), socket_path, directory)
            else:
                docker = DockerFixture(socket_path)
                try:
                    stage(docker, socket_path, directory)
                finally:
                    docker.cleanup()
        if source is None:
            print("Real runtime staging and fixed speech rollback passed. Fixture removed.")
        else:
            print("Real runtime staging passed. Old workers stopped, replacements routed, and fixture removed.")
    except FixtureError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, HTTPException, subprocess.SubprocessError,
            ControlUnavailable, JournalUnavailable, TrafficUnavailable):
        raise SystemExit("Speech runtime staging configuration or state was invalid.") from None


if __name__ == "__main__":
    main()
