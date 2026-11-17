"""Explicit recovery POST; every lost or invalid response remains uncertain."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .control_status import configuration, identifier, reject_constant, state
from .controller_client import unique_keys


RECOVERY_TIMEOUT = 20


class RecoveryTransportError(Exception):
    def __init__(self):
        super().__init__("Recovery outcome is unconfirmed. Inspect controller status and retry this saved request; do not create a new recovery ID.")


def validate_result(value):
    if (type(value) is not dict or set(value) != {"status", "reason", "state"}
            or value["status"] not in ("reconciled", "rejected", "recovery_required")
            or not identifier(value["reason"])):
        raise ValueError
    state(value["state"])
    return value


def submit_recovery(payload):
    """One bounded attempt only. Timeout does not cancel controller execution."""
    try:
        config = configuration()
        if (type(payload) is not dict or set(payload) != {"recovery_id", "expected_revision", "observed", "reason"}
                or not identifier(payload["recovery_id"]) or type(payload["expected_revision"]) is not int
                or not 0 <= payload["expected_revision"] <= 2**63 - 1
                or not isinstance(payload["reason"], str) or not payload["reason"].strip()
                or len(payload["reason"]) > 500 or any(ord(c) < 32 for c in payload["reason"])):
            raise ValueError
        state(payload["observed"])
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) > 4096:
            raise ValueError
        config["url"] = config["url"].removesuffix("/v1/status") + "/v1/recover"
        config["payload"] = payload
        worker = Path(__file__).with_name("recovery_worker.py").resolve()
        result = subprocess.run([sys.executable, "-I", str(worker)], input=json.dumps(config).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=RECOVERY_TIMEOUT, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode != 0 or len(result.stdout) > 65536:
            raise ValueError
        value = validate_result(json.loads(result.stdout, object_pairs_hook=unique_keys, parse_constant=reject_constant))
        if value["status"] == "reconciled" and (value["reason"] != "operator_reconciled"
                or state(value["state"]) != state(payload["observed"])):
            raise ValueError
        return value
    except Exception:
        raise RecoveryTransportError() from None
