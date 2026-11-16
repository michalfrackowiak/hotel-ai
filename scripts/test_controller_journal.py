"""Private disposable SQLite journals and fictional backends only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.journal import DurableLifecycleExecutor, JournalUnavailable
from speech_controller.lifecycle import Deployment, SwitchRequest
from test_controller_lifecycle import Backend, DEPLOYMENTS, ROUTES


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "controller.sqlite3"
        self.backend = Backend()

    def executor(self, **kwargs):
        return DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, self.path, **kwargs)

    def request(self, name="one", revision=0, slot=("en", "stt")):
        return SwitchRequest(name, revision, slot, "new-stt")

    def test_restart_replays_historical_outcome_without_effects(self):
        executor = self.executor()
        first = executor.execute(self.request())
        second = executor.execute(self.request("two", 1, ("pl", "stt")))
        self.assertEqual(second.state.revision, 2)
        calls = list(self.backend.calls)
        restarted = self.executor()
        self.assertEqual(restarted.state, second.state)
        self.assertEqual(restarted.execute(self.request()), first)
        self.assertEqual(restarted.execute(self.request("one", 1)).reason, "request_id_conflict")
        self.assertEqual(self.backend.calls, calls)
        self.assertFalse(restarted.recovery_status()["blocked"])

    def test_interrupted_partial_start_blocks_restart_and_retry(self):
        class Crash(BaseException):
            pass

        executor = self.executor()
        with patch.object(self.backend, "start", side_effect=Crash):
            with self.assertRaises(Crash):
                executor.execute(self.request())
        restarted = self.executor()
        calls = list(self.backend.calls)
        self.assertEqual(restarted.execute(self.request()).status, "recovery_required")
        self.assertEqual(restarted.execute(self.request("other")).reason, "reconciliation_required")
        self.assertEqual(restarted.recovery_status()["pending_requests"], ("one",))
        self.assertEqual(self.backend.calls, calls)
        self.assertEqual(restarted.state.revision, 0)

    def test_finalize_storage_failure_leaves_pending_after_effects(self):
        executor = self.executor()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_finish BEFORE UPDATE ON operations BEGIN SELECT RAISE(ABORT, 'secret failure'); END")
            db.commit()
        with self.assertRaises(JournalUnavailable) as caught:
            executor.execute(self.request())
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn(("start", "new-stt"), self.backend.calls)
        restarted = self.executor()
        self.assertTrue(restarted.recovery_status()["blocked"])
        self.assertEqual(restarted.state.revision, 0)
        self.assertEqual(restarted.execute(self.request()).reason, "pending_reconciliation")

    def test_two_instances_never_overlap_backend_work(self):
        first, second = self.executor(), self.executor()
        entered, release = Event(), Event()
        original = self.backend.revalidate

        def wait(current, destination):
            entered.set()
            release.wait(3)
            return original(current, destination)

        with patch.object(self.backend, "revalidate", side_effect=wait), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first.execute, self.request())
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(second.execute(self.request("other")).reason, "busy")
                self.assertFalse(self.backend.calls)
            finally:
                release.set()
            self.assertEqual(future.result().status, "succeeded")

    def test_catalog_drift_journal_limits_and_recovery_latch_survive_restart(self):
        executor = self.executor(journal_limit=1)
        self.backend.not_ready.add("new-stt")
        self.backend.fail.add(("stop", "new-stt"))
        result = executor.execute(self.request())
        self.assertEqual(result.status, "recovery_required")
        restarted = self.executor(journal_limit=1)
        self.assertEqual(restarted.execute(self.request()), result)
        self.assertEqual(restarted.execute(self.request("other")).reason, "reconciliation_required")
        self.assertTrue(restarted.recovery_status()["blocked"])
        with self.assertRaises(JournalUnavailable):
            self.executor(journal_limit=2)
        with self.assertRaises(JournalUnavailable):
            DurableLifecycleExecutor(self.backend, DEPLOYMENTS + (Deployment("extra", "stt", ("en",)),),
                                     ROUTES, self.path, journal_limit=1)
        other = Path(self.directory.name) / "full.sqlite3"
        executor = DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, other, journal_limit=1)
        noop = SwitchRequest("noop", 0, ("en", "stt"), "shared-stt")
        executor.execute(noop)
        restarted = DurableLifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, other, journal_limit=1)
        self.assertEqual(restarted.execute(self.request()).reason, "journal_full")

    def test_corruption_and_foreign_database_fail_before_backend(self):
        self.path.write_text("secret not sqlite")
        with self.assertRaises(JournalUnavailable) as caught:
            self.executor()
        self.assertNotIn("secret", str(caught.exception))
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE guests (value TEXT)")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            self.executor()
        self.path.unlink()
        executor = self.executor()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE metadata SET state='{}'")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            executor.execute(self.request())
        self.assertFalse(self.backend.calls)

    def test_incompatible_metadata_identity_is_rejected(self):
        executor = self.executor()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("ALTER TABLE metadata RENAME TO old_metadata")
            db.execute("CREATE TABLE metadata AS SELECT 2 AS id,fingerprint,journal_limit,state,blocked FROM old_metadata")
            db.execute("DROP TABLE old_metadata")
            db.commit()
        with self.assertRaises(JournalUnavailable):
            executor.execute(self.request())
        self.assertFalse(self.backend.calls)


if __name__ == "__main__":
    unittest.main()
