"""Atomic local route publication and claims; no lifecycle journal integration."""
from dataclasses import asdict
import hashlib
import json
from types import MappingProxyType
from time import monotonic, sleep

from .lifecycle import Deployment, LifecycleExecutor, State
from .readiness import ProbeTarget, unique_keys
from .traffic import SCHEMA, TrafficGate, TrafficUnavailable, _Claim, _initialize


ROUTED_SCHEMA = SCHEMA | {
    "metadata": "CREATE TABLE metadata (version INTEGER NOT NULL, fingerprint TEXT NOT NULL, revision INTEGER NOT NULL)",
    "routes": "CREATE TABLE routes (language TEXT NOT NULL, task TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(language, task))",
}


def _catalog(targets):
    targets = tuple(targets)
    if (not 1 <= len(targets) <= 8 or any(not isinstance(target, ProbeTarget) for target in targets)
            or len({target.name for target in targets}) != len(targets)):
        raise ValueError("One to eight unique locally approved route targets are required.")
    canonical = [asdict(target) | {"languages": sorted(target.languages)}
                 for target in sorted(targets, key=lambda item: item.name)]
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return MappingProxyType({target.name: target for target in targets}), fingerprint


def load_targets(raw):
    """Parse trusted local JSON only; never guest/admin-supplied endpoints."""
    try:
        if type(raw) is not str or len(raw) > 16384:
            raise ValueError
        values = json.loads(raw, object_pairs_hook=unique_keys)
        if type(values) is not list or not 1 <= len(values) <= 8:
            raise ValueError
        targets = []
        for value in values:
            if (type(value) is not dict or set(value) != {"name", "task", "base_url", "languages"}
                    or type(value["languages"]) is not list):
                raise ValueError
            targets.append(ProbeTarget(**(value | {"languages": tuple(value["languages"])})))
        _catalog(targets)
        return tuple(targets)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Invalid locally approved route targets.") from None


def _state(targets, revision, routes):
    if type(revision) is not int or not 0 <= revision <= 2**63 - 1:
        raise ValueError("Invalid durable route revision.")
    deployments = tuple(Deployment(target.name, target.task, target.languages) for target in targets.values())
    return LifecycleExecutor(None, deployments, routes, revision=revision).state


def initialize_routing(path, targets, routes, revision=0):
    """Explicitly create a new v2 store; never migrate or replace live claims."""
    targets, fingerprint = _catalog(targets)
    state = _state(targets, revision, routes)

    def seed(db):
        db.execute("INSERT INTO metadata VALUES (2,?,?)", (fingerprint, state.revision))
        db.executemany("INSERT INTO routes VALUES (?,?,?)", [(lang, task, name) for (lang, task), name in state.routes])

    _initialize(path, ROUTED_SCHEMA, seed)


class _RoutedClaim(_Claim):
    def __init__(self, gate, slot, target, revision):
        super().__init__(gate, slot)
        self._selection = (target, revision)

    @property
    def target(self):
        return self._selection[0]

    @property
    def route_revision(self):
        return self._selection[1]


class RoutedTrafficGate(TrafficGate):
    _schema = ROUTED_SCHEMA

    def __init__(self, path, targets, *, timeout=5):
        self._targets, self._fingerprint = _catalog(targets)
        super().__init__(path, timeout=timeout)

    @property
    def deployments(self):
        return tuple(Deployment(target.name, target.task, target.languages) for target in self._targets.values())

    @property
    def binding(self):
        """Pin this catalog and physical store to its controller journal."""
        return (str(self._path), *self._identity, self._fingerprint)

    def _read_state(self, db):
        metadata = db.execute("SELECT version,fingerprint,revision FROM metadata LIMIT 2").fetchall()
        if len(metadata) != 1 or metadata[0][:2] != (2, self._fingerprint):
            raise ValueError
        rows = db.execute("SELECT language,task,target FROM routes LIMIT 5").fetchall()
        if len(rows) != 4:
            raise ValueError
        return _state(self._targets, metadata[0][2], {(lang, task): name for lang, task, name in rows})

    def _validate_metadata(self, db):
        self._read_state(db)

    def _new_claim(self, db, slot):
        state = self._read_state(db)
        return _RoutedClaim(self, slot, self._targets[dict(state.routes)[slot]], state.revision)

    @property
    def state(self):
        with self._transaction() as db:
            return self._read_state(db)

    def _quiescent(self, db, expected):
        return (self._read_state(db) == expected
                and not db.execute("SELECT 1 FROM slots WHERE closed=0 LIMIT 1").fetchone()
                and not db.execute("SELECT 1 FROM claims LIMIT 1").fetchone())

    def quiescent(self, expected):
        """Check exact routes, closed admission, and absence of every claim."""
        try:
            with self._transaction() as db:
                return self._quiescent(db, expected)
        except TrafficUnavailable:
            return False

    def drain_all(self, expected):
        """Close all slots atomically, then wait within one total drain budget."""
        deadline = monotonic() + self._timeout
        try:
            with self._transaction(deadline) as db:
                if self._read_state(db) != expected:
                    return False
                db.execute("UPDATE slots SET closed=1")
            while True:
                with self._transaction(deadline) as db:
                    if self._quiescent(db, expected):
                        return True
                    if self._read_state(db) != expected:
                        return False
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                sleep(min(0.025, remaining))
        except TrafficUnavailable:
            return False

    def resume_all(self, expected):
        """Open all slots only for the exact closed, empty route snapshot."""
        try:
            with self._transaction() as db:
                if not self._quiescent(db, expected):
                    return False
                db.execute("UPDATE slots SET closed=0")
            return True
        except TrafficUnavailable:
            return False

    def publish(self, current, destination):
        """Compare-and-swap exactly one drained slot; admission stays closed."""
        try:
            for value in (current, destination):
                if (not isinstance(value, State) or type(value.routes) is not tuple
                        or _state(self._targets, value.revision, dict(value.routes)) != value):
                    return False
            if destination.revision != current.revision + 1:
                return False
            old, new = dict(current.routes), dict(destination.routes)
            changed = [slot for slot in old if old[slot] != new[slot]]
            if len(changed) != 1:
                return False
            slot = changed[0]
            with self._transaction() as db:
                if self._read_state(db) != current:
                    return False
                closed = db.execute("SELECT closed FROM slots WHERE language=? AND task=?", slot).fetchone()[0]
                if not closed or db.execute("SELECT 1 FROM claims WHERE language=? AND task=? LIMIT 1", slot).fetchone():
                    return False
                db.execute("UPDATE routes SET target=? WHERE language=? AND task=?", (new[slot], *slot))
                db.execute("UPDATE metadata SET revision=?", (destination.revision,))
            return True
        except (ValueError, TypeError, KeyError, TrafficUnavailable):
            return False
