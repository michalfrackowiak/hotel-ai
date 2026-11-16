"""Durable fail-closed wrapper; interrupted operations require reconciliation.

Use a dedicated database in a private local directory, never the guest database.
No lease expiry, automatic replay, reconciliation or Docker integration is supplied.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from .lifecycle import LifecycleExecutor, State, SwitchRequest, SwitchResult, identifier


class JournalUnavailable(Exception):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class DurableLifecycleExecutor:
    def __init__(self, backend, deployments, routes, path, *, revision=0, journal_limit=128):
        self._backend = backend
        self._deployments = tuple(deployments)
        initial = LifecycleExecutor(backend, self._deployments, routes,
                                    revision=revision, journal_limit=journal_limit).state
        self._path = Path(path)
        if not self._path.is_absolute() or self._path.is_symlink():
            raise ValueError("Use a dedicated absolute journal file path.")
        self._limit = journal_limit
        catalog = sorted((item.name, item.task, sorted(item.languages)) for item in self._deployments)
        self._fingerprint = hashlib.sha256(encode(catalog).encode()).hexdigest()
        with self._transaction() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                db.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), fingerprint TEXT NOT NULL, journal_limit INTEGER NOT NULL, state TEXT NOT NULL, blocked INTEGER NOT NULL CHECK(blocked IN (0,1)))")
                db.execute("CREATE TABLE operations (request_id TEXT PRIMARY KEY, request TEXT NOT NULL, outcome TEXT)")
                db.execute("INSERT INTO metadata VALUES (1,?,?,?,0)",
                           (self._fingerprint, self._limit, encode(asdict(initial))))
            elif tables != {"metadata", "operations"}:
                raise ValueError("Unrecognized journal.")
            self._load(db)

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
                or value["status"] not in {"succeeded", "unchanged", "rejected", "rolled_back", "recovery_required", "cleanup_required"}
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
