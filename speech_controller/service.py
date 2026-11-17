"""Loopback-only, authenticated, read-only Docker inventory. No lifecycle actions."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import platform
import re
import stat
import subprocess
import threading
from time import monotonic

from .resources import host_resources


IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
DAEMON_ID = re.compile(r"[a-zA-Z0-9:_-]{1,128}\Z")
INFO_FORMAT = '{"id":{{json .ID}},"name":{{json .Name}},"os_type":{{json .OSType}}}'
INSPECT_FORMAT = (
    '{"container_name":{{json .Name}},"id":{{json .Id}},'
    '"state":{{json .State.Status}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}"none"{{end}},'
    '"image_id":{{json .Image}},"configured_image":{{json .Config.Image}},'
    '"nano_cpus":{{json .HostConfig.NanoCpus}},"memory_bytes":{{json .HostConfig.Memory}}}'
)


@dataclass(frozen=True)
class Configuration:
    token: str = field(repr=False)
    host: str
    containers: tuple
    port: int = 8092
    storage_path: str = field(default="", repr=False)
    docker_socket: str = field(default="", repr=False)
    expected_daemon_id: str = ""

    @classmethod
    def from_env(cls, env):
        try:
            token = env.get("HOTEL_SPEECH_CONTROLLER_TOKEN", "")
            host = env.get("HOTEL_SPEECH_CONTROLLER_HOST", "")
            containers = json.loads(env.get("HOTEL_SPEECH_CONTROLLER_CONTAINERS", "[]"))
            port = int(env.get("HOTEL_SPEECH_CONTROLLER_PORT", "8092"))
            storage_path = env.get("HOTEL_SPEECH_CONTROLLER_STORAGE_PATH", "")
            docker_socket = env.get("HOTEL_SPEECH_CONTROLLER_DOCKER_SOCKET", "")
            expected_daemon_id = env.get("HOTEL_SPEECH_CONTROLLER_EXPECTED_DAEMON_ID", "")
            if (not 32 <= len(token) <= 256 or not token.isascii()
                    or any(not 33 <= ord(char) <= 126 for char in token)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", host)
                    or not isinstance(containers, list) or not 1 <= len(containers) <= 8
                    or any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in containers)
                    or len(set(containers)) != len(containers) or not 1 <= port <= 65535
                    or storage_path and (not os.path.isabs(storage_path) or any(ord(c) < 32 for c in storage_path))):
                raise ValueError
            if bool(docker_socket) != bool(expected_daemon_id):
                raise ValueError
            if docker_socket:
                if (not DAEMON_ID.fullmatch(expected_daemon_id) or platform.system() != "Linux"
                        or not os.path.isabs(docker_socket) or any(ord(c) < 32 for c in docker_socket)
                        or not stat.S_ISSOCK(os.lstat(docker_socket).st_mode)):
                    raise ValueError
        except (OSError, ValueError, TypeError):
            raise ValueError("Invalid controller configuration: check token, host, containers, port, absolute storage path and paired local socket/daemon ID.") from None
        return cls(token, host, tuple(containers), port, storage_path, docker_socket, expected_daemon_id)


class InventoryUnavailable(Exception):
    pass


def inventory(config):
    """Only allowlisted names enter a fixed argv; Docker output is never forwarded."""
    try:
        deadline = monotonic() + 5
        if bool(config.docker_socket) != bool(config.expected_daemon_id):
            raise ValueError
        argv, environment = ["docker"], os.environ.copy()
        if config.docker_socket:
            if platform.system() != "Linux" or not stat.S_ISSOCK(os.lstat(config.docker_socket).st_mode):
                raise ValueError
            argv += ["--host", "unix://" + config.docker_socket]
            for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_TLS"):
                environment.pop(key, None)

        def docker(arguments):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError
            return subprocess.run(argv + arguments, shell=False, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=remaining, check=True)

        info = docker(["info", "--format", INFO_FORMAT])
        if len(info.stdout) > 4096:
            raise ValueError
        identity = json.loads(info.stdout)
        if (not isinstance(identity, dict) or set(identity) != {"id", "name", "os_type"}
                or not isinstance(identity["id"], str) or not DAEMON_ID.fullmatch(identity["id"])
                or not isinstance(identity["name"], str) or not 1 <= len(identity["name"]) <= 128
                or any(ord(c) < 32 for c in identity["name"])
                or identity["os_type"] not in ("linux", "windows")
                or config.expected_daemon_id and identity["id"] != config.expected_daemon_id):
            raise ValueError
        identity["connection"] = "pinned_local_socket" if config.docker_socket else "unverified_context"
        result = docker(["inspect", "--type", "container", "--format", INSPECT_FORMAT, *config.containers])
        if len(result.stdout) > 65536:
            raise ValueError
        records = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
        if len(records) != len(config.containers):
            raise ValueError
        deployments = {}
        expected = {"container_name", "id", "state", "health", "image_id", "configured_image", "nano_cpus", "memory_bytes"}
        for record in records:
            if not isinstance(record, dict) or set(record) != expected:
                raise ValueError
            for field in expected - {"nano_cpus", "memory_bytes"}:
                if not isinstance(record[field], str) or len(record[field]) > 512:
                    raise ValueError
            name = record["container_name"].removeprefix("/")
            if name not in config.containers or name in deployments:
                raise ValueError
            if (not re.fullmatch(r"[a-f0-9]{64}", record["id"])
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", record["image_id"])
                    or record["state"] not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
                    or record["health"] not in {"none", "starting", "healthy", "unhealthy"}
                    or not record["configured_image"]):
                raise ValueError
            for field in ("nano_cpus", "memory_bytes"):
                if type(record[field]) is not int or not 0 <= record[field] <= 2**63 - 1:
                    raise ValueError
            deployments[name] = {
                "container_name": name, "id": record["id"], "state": record["state"],
                "health": record["health"], "image_id": record["image_id"],
                "configured_image": record["configured_image"],
                "cpu_limit": record["nano_cpus"] / 1_000_000_000 or None,
                "memory_mib": record["memory_bytes"] / 1048576 or None,
            }
        return {"host": config.host, "observed_at": datetime.now(timezone.utc).isoformat(),
                "daemon_identity": identity,
                "host_resources": host_resources(config.storage_path),
                "deployments": [deployments[name] for name in config.containers]}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, RecursionError):
        raise InventoryUnavailable("Inventory unavailable.") from None


class InventoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, *, port=None, handler=None):
        self.config = config
        self.inventory_lock = threading.Lock()
        self.workers = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", config.port if port is None else port), handler or InventoryHandler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(3)
        return request, address

    def process_request(self, request, address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.workers.release()


class InventoryHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if status == 429:
            self.send_header("Retry-After", "1")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def handle_request(self):
        supplied = self.headers.get("Authorization", "").encode("utf-8")
        expected = ("Bearer " + self.server.config.token).encode("ascii")
        if not hmac.compare_digest(supplied, expected):
            self.reply(401, {"error": "Unauthorized."})
            return
        if self.command != "GET":
            self.reply(405, {"error": "GET required."})
            return
        if self.path != "/v1/inventory":
            self.reply(404, {"error": "Not found."})
            return
        if not self.server.inventory_lock.acquire(blocking=False):
            self.reply(429, {"error": "Inventory busy."})
            return
        try:
            try:
                result = inventory(self.server.config)
            except InventoryUnavailable:
                self.reply(503, {"error": "Inventory unavailable."})
            else:
                self.reply(200, result)
        finally:
            self.server.inventory_lock.release()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_request


def main():
    try:
        config = Configuration.from_env(os.environ)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    with InventoryServer(config) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
