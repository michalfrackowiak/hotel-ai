"""Explicit recovery POST; every lost or invalid response remains uncertain."""
from .control_operation import OperationResponseError, post_operation
from .control_status import ControlStatusError, identifier, state


RECOVERY_TIMEOUT = 20


class RecoveryTransportError(Exception):
    def __init__(self):
        super().__init__("Recovery outcome is unconfirmed. Inspect controller status and retry this saved request; do not create a new recovery ID.")


def submit_recovery(payload):
    """One bounded attempt only. Timeout does not cancel controller execution."""
    try:
        if (type(payload) is not dict or set(payload) != {"recovery_id", "expected_revision", "observed", "reason"}
                or not identifier(payload["recovery_id"]) or type(payload["expected_revision"]) is not int
                or not 0 <= payload["expected_revision"] <= 2**63 - 1
                or not isinstance(payload["reason"], str) or not payload["reason"].strip()
                or len(payload["reason"]) > 500 or any(ord(c) < 32 for c in payload["reason"])):
            raise ValueError
        state(payload["observed"])
        value = post_operation("/v1/recover", payload, RECOVERY_TIMEOUT)
        if value["status"] not in ("reconciled", "rejected", "recovery_required"):
            raise ValueError
        if value["status"] == "reconciled" and (value["reason"] != "operator_reconciled"
                or state(value["state"]) != state(payload["observed"])):
            raise ValueError
        return value
    except (OperationResponseError, ControlStatusError, ValueError, TypeError, RecursionError):
        raise RecoveryTransportError() from None
