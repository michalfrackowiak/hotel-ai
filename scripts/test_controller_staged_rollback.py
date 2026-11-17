"""The hosted fixture backups leave the source and first snapshot intact."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from smoke_speech_runtime import snapshot_sqlite
from verify_recovery import database_checks, digest


class SnapshotTests(unittest.TestCase):
    def test_exclusive_snapshot_and_restore_preserve_source_bytes(self):
        with TemporaryDirectory() as directory:
            source = Path(directory).resolve() / "bookings.sqlite3"
            backup = source.with_name("backup.sqlite3")
            restored = source.with_name("restored.sqlite3")
            with closing(sqlite3.connect(source)) as database:
                database.execute("CREATE TABLE fixture (value TEXT NOT NULL)")
                database.execute("INSERT INTO fixture VALUES ('fictional-draft')")
                database.commit()
            source_digest = digest(source)
            snapshot_sqlite(source, backup)
            backup_digest = digest(backup)
            snapshot_sqlite(backup, restored)
            with self.assertRaises(FileExistsError):
                snapshot_sqlite(restored, backup)
            with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as database:
                self.assertEqual(database.execute("SELECT value FROM fixture").fetchall(), [("fictional-draft",)])
            self.assertEqual(database_checks(restored), (True, True, {"fixture": 1}))
            self.assertEqual(digest(source), source_digest)
            self.assertEqual(digest(backup), backup_digest)
