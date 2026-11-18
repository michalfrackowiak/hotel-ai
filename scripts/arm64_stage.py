"""Live-host boundaries for the disposable speech runtime drill."""
from contextlib import closing, contextmanager
from http.client import HTTPConnection, HTTPException
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import stat
from threading import Event, Thread
from time import monotonic


SERVICES = ("web", "piper", "asr")
PRODUCTION_INSPECT = (
    '{"id":{{json .Id}},"image":{{json .Image}},"started":{{json .State.StartedAt}},'
    '"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
)
ROUTED_INSPECT = PRODUCTION_INSPECT.replace("if .State.Health", 'if index .State "Health"')[:-1] + (
    ',"status":{{json .State.Status}},"cpu":{{json .HostConfig.NanoCpus}}}'
)


class FixtureError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise FixtureError(message)


def arm64_source(path, script_root):
    require(platform.system() == "Linux" and platform.machine() == "aarch64" and os.getuid() != 0,
            "ARM64 staging requires an ordinary account on a native ARM64 Linux host.")
    require(path is not None and Path(path).is_absolute(), "Supply the absolute staged candidate source path.")
    source = Path(path).resolve(strict=True)
    require(str(source) == path and source == script_root and source == Path.cwd().resolve(),
            "Run ARM64 staging from the reviewed candidate source directory.")
    require("," not in path and all(ord(char) >= 32 and ord(char) != 127 for char in path),
            "Candidate source path cannot be used in a Docker bind mount.")
    parts = source.relative_to("/tmp").parts if source.is_relative_to("/tmp") else ()
    require(len(parts) >= 2, "Place the candidate inside a private temporary directory under /tmp.")
    parent = (Path("/tmp") / parts[0]).lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
            "The candidate temporary directory must be owned by the staging account and mode 0700.")
    return source


def docker_path(directory):
    wrapper = directory / "docker"
    descriptor = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
    with os.fdopen(descriptor, "w", encoding="ascii") as script:
        script.write('#!/bin/sh\nexec /usr/bin/sudo -n /usr/bin/docker "$@"\n')
    return str(directory) + os.pathsep + os.defpath


@contextmanager
def cleanup_on_disconnect():
    handled = (signal.SIGHUP, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in handled}

    def interrupted(signum, frame):
        for pending_signum in handled:
            signal.signal(pending_signum, signal.SIG_IGN)
        raise FixtureError("Staging interrupted. Fixture cleanup was requested.")

    try:
        for signum in handled:
            signal.signal(signum, interrupted)
        yield
    finally:
        for signum, old_handler in previous.items():
            signal.signal(signum, old_handler)


def production_snapshot(docker):
    existing = {}
    for service in SERVICES:
        result = json.loads(docker.run("inspect", "--type", "container", "--format", PRODUCTION_INSPECT,
                                       "hotel-ai-" + service + "-1"))
        require(set(result) == {"id", "image", "started", "running", "health", "project", "service"}
                and re.fullmatch(r"[a-f0-9]{64}", result["id"])
                and re.fullmatch(r"sha256:[a-f0-9]{64}", result["image"])
                and result["project"] == "hotel-ai" and result["service"] == service
                and result["running"] is True and result["health"] == "healthy" and result["started"],
                "All three expected production services must be running and healthy.")
        existing[service] = result
    return existing


def routed_production_snapshot(docker):
    roles = (
        ("web", "web", "running", 250_000_000),
        ("piper", "routed-piper-primary", "running", 400_000_000),
        ("asr", "routed-vosk-primary", "running", 400_000_000),
        ("piper_standby", "routed-piper-standby", "created", 400_000_000),
        ("vosk_standby", "routed-vosk-standby", "created", 400_000_000),
        ("fixed_piper", "piper", "exited", 1_000_000_000),
        ("fixed_asr", "asr", "exited", 1_000_000_000),
    )
    existing = {}
    for key, service, status, cpu in roles:
        name = f"hotel-ai-{service}" if service.startswith("routed-") else f"hotel-ai-{service}-1"
        result = json.loads(docker.run("inspect", "--type", "container", "--format", ROUTED_INSPECT, name))
        require(re.fullmatch(r"[a-f0-9]{64}", result["id"])
                and re.fullmatch(r"sha256:[a-f0-9]{64}", result["image"])
                and result["project"] == "hotel-ai" and result["service"] == service
                and result["status"] == status and result["running"] is (status == "running")
                and result["started"] and result["cpu"] == cpu
                and (key != "web" or result["health"] == "healthy"),
                "Expected the production routed pilot and its verified worker limits.")
        existing[key] = result
    running_ids = docker.run("ps", "--quiet", "--no-trunc", "--filter",
                             "label=com.docker.compose.project=hotel-ai").splitlines()
    require(set(running_ids) == {record["id"] for record in existing.values() if record["running"]},
            "Unexpected running services in the production project.")
    return existing


def sample_health():
    started = monotonic()
    try:
        with closing(HTTPConnection("127.0.0.1", 8080, timeout=1)) as connection:
            connection.request("GET", "/healthz/")
            response = connection.getresponse()
            return monotonic() - started if response.status == 200 else None
    except (OSError, HTTPException):
        return None


class ProductionHealth:
    def __init__(self):
        self.abort = Event()
        self.done = Event()
        self.samples = []

    def sample(self):
        value = sample_health()
        self.samples.append(value)
        if value is None:
            self.abort.set()
        return value is not None

    def check(self):
        require(not self.abort.is_set(), "Production health failed. Staging was aborted.")

    def watch(self):
        while not self.done.wait(0.75) and self.sample():
            pass

    @contextmanager
    def monitor(self):
        for _ in range(3):
            require(self.sample(), "Production health preflight failed.")
        thread = Thread(target=self.watch, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self.done.set()
            thread.join(timeout=4)
            require(not thread.is_alive(), "Production health monitor did not stop.")

    def report(self):
        values = sorted(value for value in self.samples if value is not None)
        print("Production health " + json.dumps({
            "samples": len(self.samples), "failures": len(self.samples) - len(values),
            "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1] * 1000, 2) if values else None,
            "max_ms": round(values[-1] * 1000, 2) if values else None,
        }, sort_keys=True))
