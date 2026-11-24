"""Validate independently recorded capture evidence without opening a live store."""

import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat

from .journal import JOURNAL_SCHEMA, decode_outcome, decode_state, encode, journal_fingerprint
from .lifecycle import Deployment, SLOTS, SwitchRequest, identifier
from .readiness import unique_keys
from .routed_executor import routed_journal_identity
from .routing import ROUTED_SCHEMA, _catalog, _state, load_targets


MAX_CAPTURE_BYTES = 32768
MAX_STORE_BYTES = 16 * 1024 * 1024
CAPTURE_FIELDS = {
    "traffic_sha256", "journal_sha256", "source_traffic_path",
    "source_traffic_device", "source_traffic_inode", "targets",
}


class EvidenceRejected(ValueError):
    """The captured pair is incomplete, untrusted or unsuitable for preparation."""


def _read_file(path, limit):
    path = Path(path)
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode) or not 0 < observed.st_size <= limit:
        raise EvidenceRejected("Evidence must be a bounded regular file.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(observed, opened):
            raise EvidenceRejected("Evidence file changed before reading.")
        result = stream.read(limit + 1)
    if len(result) != opened.st_size or len(result) > limit:
        raise EvidenceRejected("Evidence file changed or exceeds its limit.")
    return result


def _capture(path):
    value = json.loads(_read_file(path, MAX_CAPTURE_BYTES), object_pairs_hook=unique_keys)
    if type(value) is not dict or value.keys() != CAPTURE_FIELDS:
        raise EvidenceRejected("Unexpected capture fields.")
    for field in ("traffic_sha256", "journal_sha256"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise EvidenceRejected("Invalid capture checksum.")
    source = value["source_traffic_path"]
    if type(source) is not str or len(source) > 4096 or any(ord(char) < 32 for char in source):
        raise EvidenceRejected("Invalid source traffic path.")
    posix = PurePosixPath(source)
    if (not posix.is_absolute() or posix.anchor != "/" or source != str(posix)
            or ".." in posix.parts or len(posix.parts) < 2):
        raise EvidenceRejected("A canonical absolute POSIX source traffic path is required.")
    device, inode = value["source_traffic_device"], value["source_traffic_inode"]
    if type(device) is not int or device < 0 or type(inode) is not int or inode < 1:
        raise EvidenceRejected("Invalid source traffic file identity.")
    return value, (source, device, inode)


def _reject_sidecars(path):
    if any(os.path.lexists(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm")):
        raise EvidenceRejected("Evidence database has a SQLite sidecar.")


def _verified_bytes(path, expected):
    _reject_sidecars(path)
    content = _read_file(path, MAX_STORE_BYTES)
    _reject_sidecars(path)
    if hashlib.sha256(content).hexdigest() != expected:
        raise EvidenceRejected("A store checksum does not match the independent capture.")
    # In the SQLite file header both format versions must be 1 (rollback-journal format).
    if not content.startswith(b"SQLite format 3\x00") or content[18:20] != b"\x01\x01":
        raise EvidenceRejected("Expected a standalone SQLite database in rollback-journal format.")
    return content


@contextmanager
def _database(content, schema):
    # Parse the exact bytes hashed above. No database path or sidecar is opened by SQLite.
    with closing(sqlite3.connect(":memory:")) as db:
        db.deserialize(content)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA query_only=ON")
        tables = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        if (tables != schema or db.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR (type='index' AND sql IS NOT NULL)"
        ).fetchone()):
            raise EvidenceRejected("Unexpected SQLite schema.")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise EvidenceRejected("SQLite integrity check failed.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise EvidenceRejected("SQLite foreign-key check failed.")
        yield db


def _traffic_state(db, targets, fingerprint):
    metadata = db.execute("SELECT version,fingerprint,revision FROM metadata LIMIT 2").fetchall()
    if len(metadata) != 1 or metadata[0][:2] != (2, fingerprint):
        raise EvidenceRejected("Unknown traffic store or mismatched target catalog.")
    rows = db.execute("SELECT language,task,target FROM routes LIMIT 5").fetchall()
    if len(rows) != len(SLOTS):
        raise EvidenceRejected("Incomplete traffic routes.")
    slots = db.execute("SELECT language,task,closed FROM slots LIMIT 5").fetchall()
    if (len(slots) != len(SLOTS) or {(lang, task) for lang, task, _ in slots} != SLOTS
            or any(type(closed) is not int or closed != 0 for _, _, closed in slots)
            or db.execute("SELECT 1 FROM claims LIMIT 1").fetchone()):
        raise EvidenceRejected("Traffic admission is closed or has retained claims.")
    return _state(targets, metadata[0][2], {(lang, task): target for lang, task, target in rows})


def _valid_audit_text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and all(
        ord(char) >= 32 for char in value
    )


def _journal_history(db, limit, state, deployments):
    operations = db.execute("SELECT request_id,request,outcome FROM operations LIMIT ?", (limit + 1,)).fetchall()
    recoveries = db.execute(
        "SELECT recovery_id,request,outcome,recorded_at,prior_state,pending_requests FROM reconciliations LIMIT ?", (limit + 1,),
    ).fetchall()
    if len(operations) > limit or len(recoveries) > limit:
        raise EvidenceRejected("Journal history exceeds its recorded limit.")
    for request_id, payload, outcome in operations:
        if outcome is None:
            raise EvidenceRejected("Journal contains a pending switch.")
        request = json.loads(payload, object_pairs_hook=unique_keys)
        if type(request) is not dict or request.get("request_id") != request_id:
            raise EvidenceRejected("Invalid switch history record.")
        request["slot"] = tuple(request["slot"])
        SwitchRequest(**request)
        if decode_outcome(json.loads(outcome, object_pairs_hook=unique_keys), deployments).state.revision > state.revision:
            raise EvidenceRejected("A switch outcome is newer than the saved state.")
    operation_ids = {request_id for request_id, _, _ in operations}
    for recovery_id, payload, outcome, recorded_at, prior, pending in recoveries:
        result = decode_outcome(json.loads(outcome, object_pairs_hook=unique_keys), deployments)
        if result.status != "reconciled" or result.reason != "operator_reconciled":
            raise EvidenceRejected("Journal contains a pending or unknown recovery outcome.")
        request = json.loads(payload, object_pairs_hook=unique_keys)
        prior_state = decode_state(json.loads(prior, object_pairs_hook=unique_keys), deployments)
        pending_ids = json.loads(pending)
        if (not identifier(recovery_id) or type(request) is not dict
                or set(request) != {"actor", "reason", "observed", "expected_revision"}
                or not _valid_audit_text(request["actor"], 120) or not _valid_audit_text(request["reason"], 500)
                or datetime.fromisoformat(recorded_at).tzinfo is None
                or type(request["expected_revision"]) is not int
                or request["expected_revision"] != prior_state.revision
                or decode_state(request["observed"], deployments) != result.state
                or not prior_state.revision <= result.state.revision <= state.revision
                or type(pending_ids) is not list or any(not identifier(item) for item in pending_ids)
                or not set(pending_ids).issubset(operation_ids)):
            raise EvidenceRejected("Invalid recovery history record.")
    return len(operations), len(recoveries)


def validate_evidence(traffic_path, journal_path, capture_path):
    """Verify a quiescent pair against independently retained capture data."""
    result, _ = _validated_capture(traffic_path, journal_path, capture_path)
    return result


def _validated_capture(traffic_path, journal_path, capture_path):
    try:
        capture, source = _capture(capture_path)
        traffic = _verified_bytes(traffic_path, capture["traffic_sha256"])
        journal = _verified_bytes(journal_path, capture["journal_sha256"])
        targets, fingerprint = _catalog(load_targets(encode(capture["targets"])))
        deployments = tuple(Deployment(target.name, target.task, target.languages) for target in targets.values())
        catalog = sorted((item.name, item.task, sorted(item.languages)) for item in deployments)
        expected = journal_fingerprint(routed_journal_identity(catalog, (*source, fingerprint)))
        with _database(traffic, ROUTED_SCHEMA) as db:
            state = _traffic_state(db, targets, fingerprint)
        with _database(journal, JOURNAL_SCHEMA) as db:
            rows = db.execute("SELECT id,fingerprint,journal_limit,state,blocked FROM metadata LIMIT 2").fetchall()
            if len(rows) != 1:
                raise EvidenceRejected("Invalid journal metadata.")
            row_id, binding, limit, saved, blocked = rows[0]
            if (row_id != 1 or binding != expected or type(limit) is not int
                    or not 1 <= limit <= 128 or type(blocked) is not int or blocked != 0):
                raise EvidenceRejected("Journal binding or limits disagree, or the journal is blocked.")
            if decode_state(json.loads(saved, object_pairs_hook=unique_keys), deployments) != state:
                raise EvidenceRejected("Saved route and journal states disagree.")
            operations, recoveries = _journal_history(db, limit, state, deployments)
        return (
            {"state": asdict(state), "completed_switches": operations, "completed_recoveries": recoveries},
            capture,
        )
    except EvidenceRejected:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise EvidenceRejected("Evidence could not be decoded or verified.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traffic", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_evidence(args.traffic, args.journal, args.capture)
    except EvidenceRejected as error:
        parser.exit(1, f"Recovery evidence rejected: {error}\n")
    print(encode(result))


if __name__ == "__main__":
    main()
