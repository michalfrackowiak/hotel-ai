"""Explicit operator recovery of existing routes, with a durable resume checkpoint."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

from .journal import encode
from .lifecycle import State, SwitchResult, identifier


PENDING_REASON = "routed_recovery_pending"


def _pending(db):
    return tuple(row[0] for row in db.execute(
        "SELECT request_id FROM operations WHERE outcome IS NULL ORDER BY request_id"))


def _recoveries(executor, db):
    return tuple(row[0] for row in db.execute("SELECT recovery_id,outcome FROM reconciliations ORDER BY recovery_id")
                 if executor._outcome(row[1]).reason == PENDING_REASON)


def recovery_status(executor):
    with executor._transaction() as db:
        state, blocked = executor._load(db)
        pending, recoveries = _pending(db), _recoveries(executor, db)
        return dict(state=state, blocked=blocked or bool(pending) or bool(recoveries),
                    pending_requests=pending, pending_recoveries=recoveries)


def recover(executor, recovery_id, expected_revision, observed, actor, reason):
    """Caller identity is audit text; this internal API does not authenticate it."""
    if (not identifier(recovery_id) or type(expected_revision) is not int or expected_revision < 0
            or not isinstance(actor, str) or not actor.strip() or len(actor) > 120
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 500
            or any(ord(char) < 32 for char in actor + reason)
            or not isinstance(observed, State) or type(observed.revision) is not int
            or not 0 <= observed.revision <= 2**63 - 1):
        raise ValueError("Invalid routed recovery request.")
    # Round-trip through journal validation, preserving the exact canonical State.
    if executor._state_from(json.loads(encode(asdict(observed)))) != observed:
        raise ValueError("A canonical observed route state is required.")
    payload = encode(dict(expected_revision=expected_revision, observed=asdict(observed), actor=actor, reason=reason))
    with executor._exclusive() as acquired:
        if not acquired:
            return SwitchResult("rejected", "busy", executor.state)
        return _recover_locked(executor, recovery_id, expected_revision, observed, payload)


def _recover_locked(executor, recovery_id, expected_revision, observed, payload):
    with executor._transaction() as db:
        current, blocked = executor._load(db)
        pending = _pending(db)
        previous = db.execute("SELECT request,outcome,prior_state,pending_requests FROM reconciliations WHERE recovery_id=?",
                              (recovery_id,)).fetchone()
        prior = current
        if previous:
            if previous[0] != payload:
                return SwitchResult("rejected", "recovery_id_conflict", current)
            outcome = executor._outcome(previous[1])
            if outcome.reason != PENDING_REASON:
                return outcome
            prior = executor._state_from(json.loads(previous[2]))
            if (outcome != SwitchResult("recovery_required", PENDING_REASON, observed)
                    or current != observed or not blocked or tuple(json.loads(previous[3])) != pending):
                raise ValueError("Inconsistent pending routed recovery.")
        elif current.revision != expected_revision:
            return SwitchResult("rejected", "stale_revision", current)
        recoveries = _recoveries(executor, db)
        if recoveries and recoveries != (recovery_id,):
            return SwitchResult("rejected", "routed_recovery_in_progress", current)
        if observed.revision < current.revision or (observed.revision == current.revision and observed != current):
            return SwitchResult("rejected", "route_state_conflict", current)
        if not blocked and not pending:
            return SwitchResult("rejected", "reconciliation_not_required", current)
        if not previous and db.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0] >= executor._limit:
            return SwitchResult("rejected", "reconciliation_history_full", current)

    traffic = executor._traffic
    try:
        # Exact-state comparison happens before the first admission change.
        if traffic.drain_all(observed) is not True:
            return SwitchResult("rejected", "routed_drain_unverified", current)
        verified = executor._backend.verify_reconciliation(prior, observed, pending) is True
        verified = verified and traffic.quiescent(observed) is True
    except Exception:
        verified = False
    if not verified:
        return SwitchResult("rejected", "reconciliation_unverified", current)

    checkpoint = SwitchResult("recovery_required", PENDING_REASON, observed)
    with executor._transaction() as db:
        if executor._load(db) != (current, blocked) or _pending(db) != pending:
            raise ValueError("Recovery journal changed during verification.")
        actual = db.execute("SELECT request,outcome,prior_state,pending_requests FROM reconciliations WHERE recovery_id=?",
                            (recovery_id,)).fetchone()
        if actual != previous or traffic.quiescent(observed) is not True:
            raise ValueError("Recovery checkpoint changed during verification.")
        if not previous:
            db.execute("INSERT INTO reconciliations VALUES (?,?,?,?,?,?)", (
                recovery_id, payload, encode(asdict(checkpoint)), datetime.now(timezone.utc).isoformat(),
                encode(asdict(prior)), encode(pending)))
        db.execute("UPDATE metadata SET state=?,blocked=1 WHERE id=1", (encode(asdict(observed)),))

    try:
        resumed = traffic.resume_all(observed) is True
    except Exception:
        resumed = False
    if not resumed:
        # Resume can raise after committing. Reclose admission; never erase claims.
        traffic.drain_all(observed)
        return checkpoint

    result = SwitchResult("reconciled", "operator_reconciled", observed)
    with executor._transaction() as db:
        if executor._load(db) != (observed, True) or _pending(db) != pending:
            raise ValueError("Recovery journal changed before completion.")
        audit = db.execute("SELECT request,outcome FROM reconciliations WHERE recovery_id=?", (recovery_id,)).fetchone()
        if audit != (payload, encode(asdict(checkpoint))) or traffic.state != observed:
            raise ValueError("Recovery state changed before completion.")
        db.execute("UPDATE metadata SET blocked=0 WHERE id=1")
        db.execute("UPDATE operations SET outcome=? WHERE outcome IS NULL", (encode(asdict(result)),))
        db.execute("UPDATE reconciliations SET outcome=? WHERE recovery_id=?", (encode(asdict(result)), recovery_id))
    return result
