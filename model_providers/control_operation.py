"""Bounded transport shared by explicit private control requests."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .control_status import configuration, identifier, reject_constant, state
from .controller_client import unique_keys


class OperationResponseError(Exception):
    """The controller's response could not be confirmed."""


def post_operation(path, payload, timeout):
    if path not in ("/v1/switch", "/v1/recover"):
        raise ValueError("Unsupported control operation.")
    config = configuration()
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 4096:
        raise ValueError("Control payload exceeds the request limit.")
    config["url"] = config["url"].removesuffix("/v1/status") + path
    config["payload"] = payload
    worker = Path(__file__).with_name("control_operation_worker.py").resolve()
    try:
        result = subprocess.run([sys.executable, "-I", str(worker)], input=json.dumps(config).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        raise OperationResponseError from None
    if result.returncode != 0 or len(result.stdout) > 65536:
        raise OperationResponseError
    value = json.loads(result.stdout, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    if (type(value) is not dict or set(value) != {"status", "reason", "state"}
            or not identifier(value["status"]) or not identifier(value["reason"])):
        raise OperationResponseError
    state(value["state"])
    return value
