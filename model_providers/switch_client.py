"""Send a saved switch request and validate its controller response."""
from .control_operation import OperationResponseError, post_operation
from .control_status import ControlStatusError, SLOTS, identifier, state


SWITCH_TIMEOUT = 20
REASONS = {
    "succeeded": {"switched"},
    "unchanged": {"already_selected"},
    "rolled_back": {"replacement_failed"},
    "rejected": {"busy", "request_id_conflict", "reconciliation_required", "journal_full", "stale_revision",
                 "unapproved_or_incompatible_target", "admission_denied", "admission_unavailable"},
    "recovery_required": {"pending_reconciliation", "route_state_mismatch", "route_state_unavailable",
                          "publication_uncertain", "rollback_incomplete", "resume_failed_after_checkpoint"},
    "cleanup_required": {"old_worker_cleanup_failed"},
    "reconciled": {"operator_reconciled"},
}


class SwitchTransportError(Exception):
    def __init__(self):
        super().__init__("Switch outcome is unconfirmed. Check live controller status and retry this saved request.")


def submit_switch(payload):
    try:
        if (type(payload) is not dict or set(payload) != {"request_id", "expected_revision", "slot", "target"}
                or not identifier(payload["request_id"]) or not identifier(payload["target"])
                or type(payload["expected_revision"]) is not int or not 0 <= payload["expected_revision"] <= 2**63 - 1
                or type(payload["slot"]) is not list or len(payload["slot"]) != 2
                or any(type(part) is not str for part in payload["slot"]) or tuple(payload["slot"]) not in SLOTS):
            raise ValueError
        value = post_operation("/v1/switch", payload, SWITCH_TIMEOUT)
        status, reason = value["status"], value["reason"]
        revision, routes = state(value["state"])
        expected = payload["expected_revision"]
        if reason not in REASONS.get(status, ()):
            raise ValueError
        if status in ("succeeded", "cleanup_required", "unchanged"):
            result_revision = expected if status == "unchanged" else expected + 1
            if revision != result_revision or routes[tuple(payload["slot"])] != payload["target"]:
                raise ValueError
        if (status == "rolled_back" and revision != expected
                or status == "reconciled" and revision < expected):
            raise ValueError
        return value
    except (OperationResponseError, ControlStatusError, ValueError, TypeError, RecursionError):
        raise SwitchTransportError() from None
