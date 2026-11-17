"""Durable traffic admission tests with real processes and fictional slot claims."""
from contextlib import closing, ExitStack
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.traffic import MAX_CLAIMS, TrafficBusy, TrafficGate, TrafficUnavailable, initialize


EN = ("en", "stt")
PL = ("pl", "stt")


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "traffic.sqlite3"
        initialize(self.path)
        self.gate = TrafficGate(self.path, timeout=0.2)

    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

    def test_explicit_initialization_and_missing_corrupt_stores_fail_closed(self):
        with self.assertRaises(FileExistsError):
            initialize(self.path)
        missing = Path(self.directory.name) / "missing.sqlite3"
        with self.assertRaises(TrafficUnavailable):
            TrafficGate(missing)
        self.assertFalse(missing.exists())
        missing.write_text("not sqlite")
        with self.assertRaises(TrafficUnavailable):
            TrafficGate(missing)
        with self.assertRaises(ValueError):
            TrafficGate("relative.sqlite3")
        for value in (0, -1, 6, True, None, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TrafficGate(self.path, timeout=value)

    def test_claim_releases_on_success_and_known_local_exception(self):
        with self.gate.claim(EN):
            self.assertEqual(self.count(), 1)
        self.assertEqual(self.count(), 0)
        with self.assertRaisesRegex(RuntimeError, "local failure"):
            with self.gate.claim(EN):
                raise RuntimeError("local failure")
        self.assertEqual(self.count(), 0)
        self.assertTrue(self.gate.drain(EN))
        with self.assertRaises(TrafficBusy):
            with TrafficGate(self.path).claim(EN):
                self.fail("closed slot admitted")
        with self.gate.claim(PL):
            pass
        self.assertTrue(self.gate.resume(EN))
        self.assertTrue(self.gate.resume(EN))
        with self.gate.claim(EN):
            pass

    def test_global_claim_cap_is_atomic_and_recovers_after_release(self):
        with ExitStack() as stack:
            for _ in range(MAX_CLAIMS):
                stack.enter_context(self.gate.claim(EN))
            with self.assertRaises(TrafficBusy):
                with TrafficGate(self.path).claim(PL):
                    self.fail("claim cap exceeded")
            self.assertEqual(self.count(), MAX_CLAIMS)
        with self.gate.claim(PL):
            self.assertEqual(self.count(), 1)
        self.assertEqual(self.count(), 0)

    def test_timeout_leaves_slot_closed_and_resume_refuses_active_claims(self):
        with self.gate.claim(EN):
            started = monotonic()
            self.assertFalse(self.gate.drain(EN))
            self.assertLess(monotonic() - started, 0.8)
            restarted = TrafficGate(self.path, timeout=0.1)
            self.assertFalse(restarted.resume(EN))
            with self.assertRaises(TrafficBusy):
                with restarted.claim(EN):
                    self.fail("draining slot admitted")
        self.assertTrue(restarted.drain(EN))
        self.assertTrue(restarted.resume(EN))

    def test_uncertain_claim_is_retained_and_closed_across_restart(self):
        with self.gate.claim(EN) as claim:
            claim.mark_uncertain()
        self.assertEqual(self.count(), 1)
        restarted = TrafficGate(self.path, timeout=0.1)
        self.assertFalse(restarted.drain(EN))
        self.assertFalse(restarted.resume(EN))
        with self.assertRaises(TrafficBusy):
            with restarted.claim(EN):
                self.fail("uncertain slot admitted")
        with restarted.claim(PL):
            pass
        self.assertEqual(self.count(), 1)

    def child(self, crash=False):
        code = (
            "import os,sys\nfrom speech_controller.traffic import TrafficGate\n"
            "with TrafficGate(sys.argv[1]).claim(('en','stt')):\n"
            " print('claimed', flush=True)\n"
            + (" os._exit(0)\n" if crash else " sys.stdin.readline()\n")
        )
        return subprocess.Popen([sys.executable, "-c", code, str(self.path)], cwd=Path(__file__).resolve().parents[1],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def test_real_process_drain_blocks_new_claims_until_holder_finishes(self):
        child = self.child()
        finished, result = Event(), []
        try:
            self.assertEqual(child.stdout.readline().strip(), "claimed")
            gate = TrafficGate(self.path, timeout=5)
            def draining():
                result.append(gate.drain(EN))
                finished.set()
            thread = Thread(target=draining)
            thread.start()
            deadline = monotonic() + 3
            while True:
                with closing(sqlite3.connect(self.path, timeout=0.1)) as db:
                    closed = db.execute("SELECT closed FROM slots WHERE language=? AND task=?", EN).fetchone()[0]
                if closed:
                    break
                if monotonic() >= deadline:
                    self.fail("drain never closed admission")
                finished.wait(0.025)
            with self.assertRaises(TrafficBusy):
                with self.gate.claim(EN):
                    self.fail("draining slot admitted")
            self.assertFalse(finished.is_set())
            with self.gate.claim(PL):
                pass
            child.communicate("finish\n", timeout=3)
            self.assertEqual(child.returncode, 0)
            self.assertTrue(finished.wait(6))
            thread.join()
            self.assertEqual(result, [True])
            self.assertTrue(gate.resume(EN))
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_crashed_process_claim_never_expires(self):
        child = self.child(crash=True)
        try:
            stdout, stderr = child.communicate(timeout=3)
            self.assertEqual((child.returncode, stdout.strip(), stderr), (0, "claimed", ""))
            self.assertEqual(self.count(), 1)
            restarted = TrafficGate(self.path, timeout=0.1)
            self.assertFalse(restarted.drain(EN))
            self.assertFalse(TrafficGate(self.path, timeout=0.1).drain(EN))
            self.assertFalse(restarted.resume(EN))
            self.assertEqual(self.count(), 1)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_locked_or_modified_store_never_admits(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            started = monotonic()
            with self.assertRaises(TrafficUnavailable):
                with self.gate.claim(EN):
                    self.fail("locked store admitted")
            self.assertFalse(self.gate.drain(EN))
            self.assertLess(monotonic() - started, 0.8)
            db.rollback()
            db.execute("DELETE FROM slots WHERE language='en' AND task='stt'")
        with self.assertRaises(TrafficUnavailable):
            with self.gate.claim(EN):
                self.fail("malformed store admitted")
        self.assertFalse(self.gate.resume(EN))


if __name__ == "__main__":
    unittest.main()
