"""Explicitly composed private lifecycle HTTP service; no production bootstrap."""
from dataclasses import asdict, dataclass, field
import hmac
import json
import re
from threading import Lock
from time import monotonic

from .lifecycle import SLOTS, SwitchRequest, identifier
from .readiness import unique_keys
from .routed_executor import RoutedLifecycleExecutor
from .service import InventoryHandler, InventoryServer


BODY_LIMIT = 4096
BODY_TIMEOUT = 3


@dataclass(frozen=True)
class ControlConfiguration:
    token: str = field(repr=False)
    actor: str
    port: int = 8093

    def __post_init__(self):
        if (not isinstance(self.token, str) or not 32 <= len(self.token) <= 256
                or any(not 33 <= ord(char) <= 126 for char in self.token)
                or not isinstance(self.actor, str) or not self.actor.strip() or len(self.actor) > 120
                or any(ord(char) < 32 or ord(char) == 127 for char in self.actor)
                or type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ValueError("Invalid control service configuration.")


def _revision(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("Invalid revision.")
    return value


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("Invalid request fields.")


def _invalid_constant(value):
    raise ValueError("Invalid JSON number.")


def _observed(executor, value):
    _fields(value, ("revision", "routes"))
    _revision(value["revision"])
    routes = value["routes"]
    if not isinstance(routes, list) or len(routes) != 4:
        raise ValueError("Four route slots are required.")
    slots = []
    for row in routes:
        if (not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], list)
                or len(row[0]) != 2 or any(type(item) is not str for item in row[0])
                or not identifier(row[1])):
            raise ValueError("Invalid route.")
        slots.append(tuple(row[0]))
    if set(slots) != SLOTS:
        raise ValueError("Every route slot must occur once.")
    return executor._state_from(value)


class ControlServer(InventoryServer):
    def __init__(self, config, executor, *, port=None):
        if not isinstance(config, ControlConfiguration) or not isinstance(executor, RoutedLifecycleExecutor):
            raise ValueError("Explicit routed controller configuration is required.")
        self.executor = executor
        self.dispatch_lock = Lock()
        super().__init__(config, port=port, handler=ControlHandler)


class ControlHandler(InventoryHandler):
    def handle(self):
        try:
            super().handle()
        except OSError:
            # A disconnected caller does not undo a journaled operation.
            pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {"error": "Invalid HTTP request."})

    def _body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get_all("Transfer-Encoding") or len(lengths) != 1
                or not re.fullmatch(r"[0-9]{1,4}", lengths[0])
                or not 1 <= int(lengths[0]) <= BODY_LIMIT
                or self.headers.get_all("Content-Type") != ["application/json"]):
            raise ValueError("Invalid request framing.")
        remaining, chunks = int(lengths[0]), []
        deadline = monotonic() + BODY_TIMEOUT
        while remaining:
            budget = deadline - monotonic()
            if budget <= 0:
                raise ValueError("Request body timed out.")
            self.connection.settimeout(budget)
            chunk = self.rfile.read1(remaining)
            if not chunk:
                raise ValueError("Incomplete request body.")
            chunks.append(chunk)
            remaining -= len(chunk)
        if monotonic() >= deadline:
            raise ValueError("Request body timed out.")
        return json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=unique_keys,
                          parse_constant=_invalid_constant)

    def _action(self, value):
        executor = self.server.executor
        if self.path == "/v1/switch":
            _fields(value, ("request_id", "expected_revision", "slot", "target"))
            if not isinstance(value["slot"], list):
                raise ValueError("Invalid slot.")
            request = SwitchRequest(value["request_id"], _revision(value["expected_revision"]),
                                    tuple(value["slot"]), value["target"])
            return lambda: executor.execute(request)
        _fields(value, ("recovery_id", "expected_revision", "observed", "reason"))
        if (not identifier(value["recovery_id"]) or not isinstance(value["reason"], str)
                or not value["reason"].strip() or len(value["reason"]) > 500
                or any(ord(char) < 32 for char in value["reason"])):
            raise ValueError("Invalid recovery request.")
        revision = _revision(value["expected_revision"])
        observed = _observed(executor, value["observed"])
        # The server's principal labels its credential, never a supplied admin identity.
        return lambda: executor.recover(value["recovery_id"], revision, observed,
                                        self.server.config.actor, value["reason"])

    def handle_request(self):
        self.close_connection = True
        supplied = self.headers.get_all("Authorization", [])
        expected = ("Bearer " + self.server.config.token).encode("ascii")
        if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode("utf-8"), expected):
            self.reply(401, {"error": "Unauthorized."})
            return
        methods = {"/v1/status": "GET", "/v1/switch": "POST", "/v1/recover": "POST"}
        if self.path not in methods:
            self.reply(404, {"error": "Not found."})
            return
        if self.command != methods[self.path]:
            self.reply(405, {"error": "Method not allowed."})
            return
        if not self.server.dispatch_lock.acquire(blocking=False):
            self.reply(429, {"error": "Controller busy."})
            return
        try:
            try:
                if self.command == "GET":
                    result = self.server.executor.recovery_status()
                    result["state"] = asdict(result["state"])
                    result["observed"] = asdict(self.server.executor.observed_state)
                else:
                    try:
                        try:
                            value = self._body()
                        finally:
                            self.connection.settimeout(3)
                        action = self._action(value)
                    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
                        self.reply(400, {"error": "Invalid request body."})
                        return
                    result = asdict(action())
            except Exception:
                self.reply(503, {"error": "Controller unavailable; inspect status before retrying."})
            else:
                self.reply(200, result)
        finally:
            self.server.dispatch_lock.release()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_request
