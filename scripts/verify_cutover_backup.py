"""Snapshot the stopped web service's SQLite database and migrate a disposable copy."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from scripts.verify_recovery import database_checks, digest, restore_copy


def main():
    os.umask(0o077)
    source = Path("/source/db.sqlite3")
    backup = Path("/backup/bookings.sqlite3")
    backup.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as original:
        with closing(sqlite3.connect(backup)) as destination:
            original.backup(destination)
    with backup.open("rb") as stream:
        os.fsync(stream.fileno())
    checksum = digest(backup)
    Path("/backup/bookings.sha256").write_text(checksum.hex() + "  bookings.sqlite3\n", encoding="ascii")
    work = Path("/tmp/work")
    work.mkdir(mode=0o700)
    _, restored = restore_copy(backup, work)
    baseline = database_checks(backup)
    if baseline[:2] != (True, True) or database_checks(restored) != baseline:
        raise RuntimeError("Snapshot integrity or restore comparison failed.")
    env = os.environ | {"DJANGO_DB_PATH": str(restored), "DJANGO_SECRET_KEY": "fictional-cutover-restore"}
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput", "--verbosity", "0"],
                   cwd="/app", env=env, capture_output=True, check=True, timeout=30)
    probe = Path("/tmp/probe")
    probe.mkdir(mode=0o700)
    subprocess.run([sys.executable, "scripts/verify_recovery.py", "--backup", str(restored),
                    "--restore-dir", str(probe)], cwd="/app", check=True, timeout=25)
    if digest(backup) != checksum:
        raise RuntimeError("The original backup digest changed.")
    # Keep a migrated copy only until the rollback image compatibility check.
    _, migrated = restore_copy(restored, Path("/backup/rollback-check"))
    if database_checks(migrated) != database_checks(restored):
        raise RuntimeError("The rollback check restore differs.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Cutover backup verification failed: " + type(error).__name__) from None
