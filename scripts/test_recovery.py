"""Exercise recovery isolation with a fictional migrated database."""

import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from verify_recovery import database_checks, digest, restore_copy


ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def test_exclusive_restore_and_read_only_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup.sqlite3"
            with closing(sqlite3.connect(backup)) as database:
                with database:
                    database.execute("CREATE TABLE fixture (value TEXT)")
                    database.execute("INSERT INTO fixture VALUES ('fictional')")
            original = digest(backup)
            destination = root / "restore"
            destination.mkdir()
            _, restored = restore_copy(backup, destination)
            self.assertEqual(database_checks(restored), database_checks(backup))
            self.assertEqual(digest(backup), original)
            with self.assertRaises(ValueError):
                restore_copy(backup, destination)
            with self.assertRaises(ValueError):
                restore_copy(backup, root)

    def test_application_restore_rolls_back_and_does_not_emit_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup.sqlite3"
            destination = root / "restore"
            destination.mkdir()
            env = {key: value for key, value in os.environ.items() if not key.startswith(("HOTEL_", "DJANGO_"))}
            env.update(DJANGO_DEBUG="1", DJANGO_DB_PATH=str(backup))
            prepared = subprocess.run(
                [sys.executable, "manage.py", "migrate", "--noinput"], cwd=ROOT, env=env,
                capture_output=True, timeout=45,
            )
            self.assertEqual(prepared.returncode, 0)
            original = digest(backup)
            # The verifier must clear inherited deployment settings before setup.
            env.update(DJANGO_DB_PATH="wrong.sqlite3", HOTEL_SMTP_HOST="forbidden.example.test")
            run = subprocess.run(
                [sys.executable, "-O", "scripts/verify_recovery.py", "--backup", str(backup), "--restore-dir", str(destination)],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(run.returncode, 0, run.stdout)
            report = json.loads(run.stdout)
            self.assertTrue(report["passed"])
            self.assertTrue(report["checks"]["probe_writes_rolled_back"])
            self.assertTrue(report["checks"]["idempotent_replay"])
            self.assertNotIn("recovery@example.test", run.stdout + run.stderr)
            self.assertEqual(digest(backup), original)
            self.assertEqual(database_checks(backup), database_checks(destination / "restored.sqlite3"))


if __name__ == "__main__":
    unittest.main()
