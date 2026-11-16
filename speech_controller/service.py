"""Loopback-only, authenticated, read-only Docker inventory. No lifecycle actions."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import re
import subprocess
import threading

from .resources import host_resources


IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
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

    @classmethod
    def from_env(cls, env):
        try:
            token = env.get("HOTEL_SPEECH_CONTROLLER_TOKEN", "")
            host = env.get("HOTEL_SPEECH_CONTROLLER_HOST", "")
            containers = json.loads(env.get("HOTEL_SPEECH_CONTROLLER_CONTAINERS", "[]"))
            port = int(env.get("HOTEL_SPEECH_CONTROLLER_PORT", "8092"))
            storage_path = env.get("HOTEL_SPEECH_CONTROLLER_STORAGE_PATH", "")
            if (not 32 <= len(token) <= 256 or not token.isascii()
                    or any(not 33 <= ord(char) <= 126 for char in token)
                    or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", host)
                    or not isinstance(containers, list) or not 1 <= len(containers) <= 8
                    or any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in containers)
                    or len(set(containers)) != len(containers) or not 1 <= port <= 65535
                    or storage_path and (not os.path.isabs(storage_path) or any(ord(c) < 32 for c in storage_path))):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Invalid controller configuration: set a strong ASCII token, host label, 1–8 unique container identifiers, valid port and optional absolute storage path.") from None
        return cls(token, host, tuple(containers), port, storage_path)


class InventoryUnavailable(Exception):
    pass


def inventory(config):
    """Only allowlisted names enter a fixed argv; Docker output is never forwarded."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", "--format", INSPECT_FORMAT, *config.containers],
            shell=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True,
        )
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
                "host_resources": host_resources(config.storage_path),
                "deployments": [deployments[name] for name in config.containers]}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        raise InventoryUnavailable("Inventory unavailable.") from None


class InventoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, *, port=None):
        self.config = config
        self.inventory_lock = threading.Lock()
        self.workers = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", config.port if port is None else port), InventoryHandler)

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
