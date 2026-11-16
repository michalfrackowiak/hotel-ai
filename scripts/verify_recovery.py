"""Verify a private SQLite backup on another host without contacting services.

Uses the installed Django dependencies and this checkout. All application probes
run in a rolled-back transaction on a new disposable copy, never on the backup.
Only aggregate verification results are emitted. This is an in-process Django
recovery check, not a Docker, speech-model, DNS, TLS or credential restore drill.
Restrict the restore directory to its owner before use (Windows requires an ACL;
the file mode alone does not establish Windows privacy).
"""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from time import monotonic
from unittest.mock import patch
from uuid import uuid4


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").digest()


def require(condition):
    if not condition:
        raise RuntimeError("Recovery verification condition failed.")


def database_checks(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        integrity = database.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        foreign_keys = not database.execute("PRAGMA foreign_key_check").fetchall()
        tables = [row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {
            name: database.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for name in tables
        }
    return integrity, foreign_keys, counts


def restore_copy(backup, restore_dir):
    backup = backup.resolve(strict=True)
    restore_dir = restore_dir.resolve(strict=True)
    if not backup.is_file() or not restore_dir.is_dir() or any(restore_dir.iterdir()):
        raise ValueError("A backup file and empty restore directory are required.")
    destination = restore_dir / "restored.sqlite3"
    # Exclusive creation prevents accidental replacement of an existing database.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
    return backup, destination


def application_checks(destination):
    if "django.conf" in sys.modules:
        raise RuntimeError("Run recovery verification in a fresh Python process.")
    for key in list(os.environ):
        if key.startswith(("DJANGO_", "HOTEL_")) or key in ("BOOKING_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ.pop(key, None)
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.settings", DJANGO_DEBUG="1",
        DJANGO_SECRET_KEY=uuid4().hex + uuid4().hex,
        DJANGO_DB_PATH=str(destination), DJANGO_ALLOWED_HOSTS="testserver",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.disable(logging.CRITICAL)
    # Fail closed if any import, signal, backend or probe attempts outbound I/O.
    with patch("socket.socket.connect", side_effect=RuntimeError("Network disabled in recovery drill")), \
            patch("socket.socket.connect_ex", side_effect=RuntimeError("Network disabled in recovery drill")):
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection, transaction
        from django.db.migrations.executor import MigrationExecutor
        from django.test import Client
        from django.utils import timezone as django_timezone
        from bookings.models import BookingConfirmation, Price, Room
        from bookings.services import confirm_booking

        call_command("check", stdout=io.StringIO(), stderr=io.StringIO())
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise RuntimeError("Restore has unapplied migrations; no automatic migration permitted.")
        result = {"system_check": True, "migrations_current": True, "languages": {}}
        with transaction.atomic():
            for language in ("en", "pl"):
                client = Client(enforce_csrf_checks=True)
                page = client.get("/?lang=" + language)
                require(page.status_code == 200)
                token = page.cookies["csrftoken"].value
                require(client.get("/healthz/").json() == {"status": "ok"})
                response = client.post(
                    "/reception/message/", {"language": language, "start": True},
                    content_type="application/json", headers={"X-CSRFToken": token},
                )
                require(response.status_code == 200 and response.json()["language"] == language)
                result["languages"][language] = {"page": True, "chat_start": True, "health": True}
            room = Room.objects.create(code="RECOVERY-" + uuid4().hex[:20], capacity=2)
            Price.objects.create(room=room, nightly_rate=Decimal("120.00"))
            arrival = django_timezone.localdate() + timedelta(days=30)
            fields = dict(
                room_id=room.pk, check_in=arrival, check_out=arrival + timedelta(days=2),
                guests=2, name="Fictional recovery guest", email="recovery@example.test",
                nightly_rate=Decimal("120.00"), idempotency_key=uuid4(), delivery_method="email",
            )
            booking, created = confirm_booking(**fields)
            replay, replay_created = confirm_booking(**fields)
            require(created and not replay_created and replay.pk == booking.pk)
            require(BookingConfirmation.objects.get(booking=booking).status == BookingConfirmation.Status.PENDING)
            result.update(booking_write=True, idempotent_replay=True, confirmation_pending=True)
            transaction.set_rollback(True)
        connection.close()
    return result


def verify(backup, restore_dir):
    started = monotonic()
    original_digest = digest(backup.resolve(strict=True))
    backup, destination = restore_copy(backup, restore_dir)
    source_integrity, source_foreign_keys, source_counts = database_checks(backup)
    integrity, foreign_keys, before = database_checks(destination)
    if not all((source_integrity, source_foreign_keys, integrity, foreign_keys, source_counts == before)):
        raise RuntimeError("Database verification failed before application checks.")
    result = application_checks(destination)
    integrity, foreign_keys, after = database_checks(destination)
    unchanged = digest(backup) == original_digest
    if not all((integrity, foreign_keys, before == after, unchanged)):
        raise RuntimeError("Database verification failed after application checks.")
    return {
        "schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
        "seconds": monotonic() - started, "passed": True,
        "checks": {"backup_unchanged": unchanged, "integrity": integrity, "foreign_keys": foreign_keys,
                   "source_counts_match_restore": True, "probe_writes_rolled_back": before == after, **result},
        "tables_checked": len(before),
        "limitations": ["In-process Django client; no listening HTTP server or container restore.",
                        "Speech models, credentials, notification delivery, TLS and DNS are not exercised."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--restore-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.backup, args.restore_dir)
    except Exception as error:
        # Never print exception messages or tracebacks from a private database.
        report = {"passed": False, "error_type": type(error).__name__}
    print(json.dumps(report, indent=2))
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
