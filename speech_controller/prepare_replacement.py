"""Prepare private routed speech stores from independently verified offline evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .initialize import create_private_stores, verify_stopped_workers
from .journal import encode
from .lifecycle import identifier
from .recovery_evidence import _valid_audit_text, _validated_capture
from .routing import _catalog, _state
from .runtime_config import RuntimeConfiguration, private_new_store


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record(path, record):
    with open(path, "x", encoding="utf-8", opener=_private_open) as stream:
        _write_record(stream, record)
    _sync_directory(path.parent)


def _private_open(path, flags):
    return os.open(path, flags | os.O_NOFOLLOW, 0o600)


def _write_record(stream, record):
    stream.write(encode(record) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _complete_record(path, record):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        staged = Path(stream.name)
        _write_record(stream, record)
    os.replace(staged, path)
    _sync_directory(path.parent)


def _store_digest(path):
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(config_path, token, *, traffic_path, journal_path, capture_path, audit_dir, recovery_id, reason):
    if not identifier(recovery_id) or not _valid_audit_text(reason, 500):
        raise ValueError("Invalid recovery ID or reason.")
    config = RuntimeConfiguration.load_for_initialization(config_path, token)
    record_path = private_new_store(str(Path(audit_dir) / (recovery_id + ".json")))
    traffic, journal = Path(config.traffic_path), Path(config.journal_path)
    if record_path.is_relative_to(traffic.parent) or record_path == journal:
        raise ValueError("The recovery record must remain outside the traffic mount and store paths.")

    verified, capture = _validated_capture(traffic_path, journal_path, capture_path)
    source_state = verified["state"]
    routes = dict(source_state["routes"])
    target_catalog, _ = _catalog(config.targets)
    state = _state(target_catalog, source_state["revision"], routes)
    controls = verify_stopped_workers(config)

    record = {
        "version": 1,
        "status": "preparing",
        "recovery_id": recovery_id,
        "actor": config.control.actor,
        "reason": reason,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "traffic_sha256": capture["traffic_sha256"],
            "journal_sha256": capture["journal_sha256"],
            "traffic_identity": [
                capture["source_traffic_path"], capture["source_traffic_device"], capture["source_traffic_inode"],
            ],
            **verified,
        },
        "replacement": {
            "daemon_id": config.daemon_id,
            "state": source_state,
            "traffic_path": str(traffic),
            "journal_path": str(journal),
            "workers": [
                {"name": approval.name, "container_id": approval.container_id, "image_id": approval.image_id,
                 "owner": approval.owner, "nano_cpus": approval.nano_cpus, "memory_bytes": approval.memory_bytes,
                 "network_id": binding.network_id, "address": binding.address,
                 "task": target.task, "languages": list(target.languages), "base_url": target.base_url}
                for approval, binding, target in zip(config.approvals, config.bindings, config.targets, strict=True)
            ],
        },
    }
    _record(record_path, record)
    executor = create_private_stores(config, controls, routes, revision=state.revision)
    if executor.state != state or executor.observed_state != state:
        raise ValueError("The prepared stores do not agree with verified source routes.")
    record["replacement"].update(traffic_sha256=_store_digest(traffic), journal_sha256=_store_digest(journal))
    for parent in {traffic.parent, journal.parent}:
        _sync_directory(parent)
    _complete_record(record_path, record | {"status": "prepared"})
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("config", "traffic", "journal", "capture", "audit-dir", "recovery-id", "reason"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args(argv)
    try:
        state = prepare(
            args.config, os.environ.get("HOTEL_SPEECH_CONTROL_TOKEN", ""),
            traffic_path=args.traffic, journal_path=args.journal, capture_path=args.capture,
            audit_dir=args.audit_dir, recovery_id=args.recovery_id, reason=args.reason,
        )
    except Exception:
        parser.exit(1, "Replacement preparation failed. Inspect private evidence and reserved destinations offline.\n")
    print(f"Private replacement stores prepared at revision {state.revision}.")


if __name__ == "__main__":
    main()
