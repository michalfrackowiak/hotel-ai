"""Durable fail-closed wrapper; interrupted operations require reconciliation.

Use a dedicated database in a private local directory, never the guest database.
No lease expiry, automatic replay or Docker integration is supplied.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .lifecycle import LifecycleExecutor, State, SwitchRequest, SwitchResult, identifier
from .file_lock import exclusive_lock


class JournalUnavailable(Exception):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class DurableLifecycleExecutor:
    _routed_execution = False

    def __init__(self, backend, deployments, routes, path, *, revision=0, journal_limit=128):
        if getattr(backend, "requires_routed_execution", False) is True and not self._routed_execution:
            raise ValueError("Routed traffic requires durable routed execution.")
        self._backend = backend
        self._deployments = tuple(deployments)
        initial = LifecycleExecutor(backend, self._deployments, routes,
                                    revision=revision, journal_limit=journal_limit).state
        self._path = Path(path)
        if not self._path.is_absolute() or self._path.is_symlink():
            raise ValueError("Use a dedicated absolute journal file path.")
        self._path = self._path.resolve()
        self._limit = journal_limit
        catalog = sorted((item.name, item.task, sorted(item.languages)) for item in self._deployments)
        self._fingerprint = hashlib.sha256(encode(self._journal_identity(catalog)).encode()).hexdigest()
        with self._transaction() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                db.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), fingerprint TEXT NOT NULL, journal_limit INTEGER NOT NULL, state TEXT NOT NULL, blocked INTEGER NOT NULL CHECK(blocked IN (0,1)))")
                db.execute("CREATE TABLE operations (request_id TEXT PRIMARY KEY, request TEXT NOT NULL, outcome TEXT)")
                db.execute("INSERT INTO metadata VALUES (1,?,?,?,0)",
                           (self._fingerprint, self._limit, encode(asdict(initial))))
            elif tables not in ({"metadata", "operations"}, {"metadata", "operations", "reconciliations"}):
                raise ValueError("Unrecognized journal.")
            self._load(db)
            db.execute("CREATE TABLE IF NOT EXISTS reconciliations (recovery_id TEXT PRIMARY KEY, request TEXT NOT NULL, outcome TEXT NOT NULL, recorded_at TEXT NOT NULL, prior_state TEXT NOT NULL, pending_requests TEXT NOT NULL)")

    def _journal_identity(self, catalog):
        return catalog

    @contextmanager
    def _exclusive(self):
        try:
            with exclusive_lock(str(self._path) + ".lock") as acquired:
                yield acquired
        except OSError:
            raise JournalUnavailable("Lifecycle journal lock unavailable.") from None

    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = sqlite3.connect(self._path, timeout=0.2, isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
            if db is not None:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            raise JournalUnavailable("Lifecycle journal unavailable; reconciliation may be required.") from None
        finally:
            if db is not None:
                db.close()

    def _state_from(self, value):
        if not isinstance(value, dict) or set(value) != {"revision", "routes"}:
            raise ValueError
        routes = value["routes"]
        if not isinstance(routes, list) or len(routes) != 4:
            raise ValueError
        mapping = {tuple(slot): target for slot, target in routes}
        return LifecycleExecutor(self._backend, self._deployments, mapping,
                                 revision=value["revision"]).state

    def _load(self, db):
        rows = db.execute("SELECT id,fingerprint,journal_limit,state,blocked FROM metadata").fetchall()
        if len(rows) != 1:
            raise ValueError
        row_id, fingerprint, limit, state, blocked = rows[0]
        if row_id != 1 or fingerprint != self._fingerprint or limit != self._limit or blocked not in (0, 1):
            raise ValueError
        return self._state_from(json.loads(state)), bool(blocked)

    def _outcome(self, raw):
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"status", "reason", "state"}
                or value["status"] not in {"succeeded", "unchanged", "rejected", "rolled_back", "recovery_required", "cleanup_required", "reconciled"}
                or not identifier(value["reason"])):
            raise ValueError
        return SwitchResult(value["status"], value["reason"], self._state_from(value["state"]))

    @property
    def state(self):
        """Last durably recorded state; pending backend effects may differ."""
        with self._transaction() as db:
            return self._load(db)[0]

    def recovery_status(self):
        with self._transaction() as db:
            state, blocked = self._load(db)
            pending = tuple(row[0] for row in db.execute(
                "SELECT request_id FROM operations WHERE outcome IS NULL ORDER BY request_id"))
            return {"state": state, "blocked": blocked or bool(pending), "pending_requests": pending}

    def execute(self, request):
        if not isinstance(request, SwitchRequest):
            raise ValueError("A validated switch request is required.")
        with self._exclusive() as acquired:
            if not acquired:
                return SwitchResult("rejected", "busy", self.state)
            return self._execute_locked(request)

    def _execute_locked(self, request):
        payload = encode(asdict(request))
        with self._transaction() as db:
            state, blocked = self._load(db)
            previous = db.execute("SELECT request,outcome FROM operations WHERE request_id=?", (request.request_id,)).fetchone()
            if previous:
                if previous[0] != payload:
                    return SwitchResult("rejected", "request_id_conflict", state)
                if previous[1] is None:
                    return SwitchResult("recovery_required", "pending_reconciliation", state)
                return self._outcome(previous[1])
            if blocked or db.execute("SELECT 1 FROM operations WHERE outcome IS NULL LIMIT 1").fetchone():
                return SwitchResult("rejected", "reconciliation_required", state)
            if db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] >= self._limit:
                return SwitchResult("rejected", "journal_full", state)
            db.execute("INSERT INTO operations VALUES (?,?,NULL)", (request.request_id, payload))
        # The pending record is committed before any backend call, without holding
        # a SQLite transaction over potentially slow effects. Other instances stop.
        return self._run_pending(request, payload, state)

    def _run_pending(self, request, payload, state):
        executor = LifecycleExecutor(self._backend, self._deployments, dict(state.routes), revision=state.revision)
        result = executor.execute(request)
        self._finish(request, payload, state, result)
        return result

    def _finish(self, request, payload, prior_state, result):
        with self._transaction() as db:
            state, blocked = self._load(db)
            pending = db.execute("SELECT request,outcome FROM operations WHERE request_id=?", (request.request_id,)).fetchone()
            if state != prior_state or blocked or pending != (payload, None):
                raise ValueError
            db.execute("UPDATE metadata SET state=?,blocked=? WHERE id=1", (
                encode(asdict(result.state)), int(result.status in ("recovery_required", "cleanup_required"))))
            db.execute("UPDATE operations SET outcome=? WHERE request_id=?", (encode(asdict(result)), request.request_id))

    def reconcile(self, recovery_id, expected_revision, routes, actor, reason):
        """Explicit operator action after read-only backend verification, never a reset.

        The verifier must check actual routing, all required models, drain state and
        cleanup/ownership. It must return exactly True and implement a deadline.
        """
        if (not identifier(recovery_id) or type(expected_revision) is not int or expected_revision < 0
                or not isinstance(actor, str) or not actor.strip() or len(actor) > 120
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 500
                or any(ord(char) < 32 for char in actor + reason)):
            raise ValueError("Invalid reconciliation request.")
        proposed = LifecycleExecutor(self._backend, self._deployments, routes, revision=expected_revision + 1).state
        payload = encode(dict(expected_revision=expected_revision, proposed=asdict(proposed), actor=actor, reason=reason))
        with self._exclusive() as acquired:
            if not acquired:
                return SwitchResult("rejected", "busy", self.state)
            with self._transaction() as db:
                current, blocked = self._load(db)
                previous = db.execute("SELECT request,outcome FROM reconciliations WHERE recovery_id=?", (recovery_id,)).fetchone()
                if previous:
                    return self._outcome(previous[1]) if previous[0] == payload else SwitchResult("rejected", "recovery_id_conflict", current)
                if current.revision != expected_revision:
                    return SwitchResult("rejected", "stale_revision", current)
                pending = tuple(row[0] for row in db.execute("SELECT request_id FROM operations WHERE outcome IS NULL ORDER BY request_id"))
                if not blocked and not pending:
                    return SwitchResult("rejected", "reconciliation_not_required", current)
                if db.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0] >= self._limit:
                    return SwitchResult("rejected", "reconciliation_history_full", current)
            try:
                verified = self._backend.verify_reconciliation(current, proposed, pending) is True
            except Exception:
                verified = False
            if not verified:
                return SwitchResult("rejected", "reconciliation_unverified", current)
            result = SwitchResult("reconciled", "operator_reconciled", proposed)
            with self._transaction() as db:
                actual, actual_blocked = self._load(db)
                actual_pending = tuple(row[0] for row in db.execute("SELECT request_id FROM operations WHERE outcome IS NULL ORDER BY request_id"))
                if actual != current or actual_blocked != blocked or actual_pending != pending:
                    raise ValueError
                db.execute("UPDATE metadata SET state=?,blocked=0 WHERE id=1", (encode(asdict(proposed)),))
                db.execute("UPDATE operations SET outcome=? WHERE outcome IS NULL", (encode(asdict(result)),))
                db.execute("INSERT INTO reconciliations VALUES (?,?,?,?,?,?)", (
                    recovery_id, payload, encode(asdict(result)), datetime.now(timezone.utc).isoformat(),
                    encode(asdict(current)), encode(pending)))
            return result

    def reconciliation_history(self):
        with self._transaction() as db:
            self._load(db)
            return tuple(dict(recovery_id=row[0], request=json.loads(row[1]), outcome=self._outcome(row[2]),
                              recorded_at=row[3], prior_state=self._state_from(json.loads(row[4])),
                              pending_requests=tuple(json.loads(row[5]))) for row in db.execute(
                "SELECT recovery_id,request,outcome,recorded_at,prior_state,pending_requests FROM reconciliations ORDER BY rowid LIMIT ?", (self._limit,)))
