"""Private read-only controller status; no routing or planning mutations."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from django.conf import settings
from django.utils import timezone

from .controller_client import unique_keys


FETCH_TIMEOUT = 6
SLOTS = {(language, task) for language in ("en", "pl") for task in ("stt", "tts")}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
ERRORS = {
    "disabled": "Live controller status is not configured.",
    "configuration": "Live controller status configuration is invalid.",
    "invalid": "The controller returned invalid status data.",
    "busy": "The controller is busy. Refresh after the current operation finishes.",
    "unauthorized": "The controller rejected the status credential. Check private configuration.",
    "unavailable": "Live controller status is unavailable. Check private connectivity and retry.",
}


class ControlStatusError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(ERRORS[code])


def configuration():
    url, token = settings.HOTEL_SPEECH_CONTROL_URL, settings.HOTEL_SPEECH_CONTROL_TOKEN
    if not url:
        raise ControlStatusError("disabled")
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", url) if isinstance(url, str) else None
    if (match is None or not 1 <= int(match[1]) <= 65535
            or not isinstance(token, str) or not 32 <= len(token) <= 256
            or any(not 33 <= ord(char) <= 126 for char in token)):
        raise ControlStatusError("configuration")
    return {"url": url + "/v1/status", "token": token}


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def state(value):
    if (type(value) is not dict or set(value) != {"revision", "routes"}
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**63 - 1
            or type(value["routes"]) is not list or len(value["routes"]) != 4):
        raise ValueError
    routes = {}
    for row in value["routes"]:
        if (type(row) is not list or len(row) != 2 or type(row[0]) is not list or len(row[0]) != 2
                or any(type(item) is not str for item in row[0]) or not identifier(row[1])):
            raise ValueError
        slot = tuple(row[0])
        if slot not in SLOTS or slot in routes:
            raise ValueError
        routes[slot] = row[1]
    return value["revision"], routes


def validate_status(value):
    if (type(value) is not dict or set(value) != {
            "state", "observed", "blocked", "pending_requests", "pending_recoveries"}
            or type(value["blocked"]) is not bool):
        raise ValueError
    state(value["state"])
    state(value["observed"])
    for field in ("pending_requests", "pending_recoveries"):
        ids = value[field]
        if (type(ids) is not list or len(ids) > 128 or any(not identifier(item) for item in ids)
                or len(set(ids)) != len(ids) or ids and not value["blocked"]):
            raise ValueError
    return value


def reject_constant(_value):
    raise ValueError


def fetch_control_status():
    config = configuration()
    worker = Path(__file__).with_name("control_status_worker.py").resolve()
    try:
        result = subprocess.run([sys.executable, "-I", str(worker)], input=json.dumps(config).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=FETCH_TIMEOUT, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        raise ControlStatusError("unavailable") from None
    if result.returncode != 0:
        raise ControlStatusError({2: "invalid", 3: "busy", 4: "unauthorized"}.get(result.returncode, "unavailable"))
    try:
        if len(result.stdout) > 65536:
            raise ValueError
        return validate_status(json.loads(result.stdout, object_pairs_hook=unique_keys, parse_constant=reject_constant))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ControlStatusError("invalid") from None


def control_status_report():
    try:
        snapshot = fetch_control_status()
    except ControlStatusError as error:
        return {"status_error": str(error), "status_error_code": error.code, "status_rows": []}
    revision, journal = state(snapshot["state"])
    observed_revision, observed = state(snapshot["observed"])
    mismatch = revision != observed_revision or journal != observed
    return {
        "fetched_at": timezone.now(), "journal_revision": revision, "observed_revision": observed_revision,
        "needs_attention": snapshot["blocked"] or mismatch, "route_mismatch": mismatch,
        "controller_blocked": snapshot["blocked"], "pending_requests": snapshot["pending_requests"],
        "pending_recoveries": snapshot["pending_recoveries"],
        "status_rows": [dict(language="English" if language == "en" else "Polish", task=task.upper(),
            journal=journal[(language, task)], observed=observed[(language, task)],
            matches=journal[(language, task)] == observed[(language, task)]) for language, task in sorted(SLOTS)],
    }
