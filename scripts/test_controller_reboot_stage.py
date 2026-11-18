"""Exercise the staged restart's read-only refusal before worker effects."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from arm64_stage import FixtureError
from smoke_speech_runtime import restart_workers, stopped_restart_state
from speech_controller.routed_recovery import PENDING_REASON


ROUTES = [[(language, task), task + "-selected"] for language in ("en", "pl") for task in ("stt", "tts")]
STATE = json.loads(json.dumps({"revision": 4, "routes": ROUTES}))


class RestartStageTests(unittest.TestCase):
    def test_pending_or_closed_state_refuses_worker_changes_and_preserves_stores(self):
        cases = (
            ("traffic", "INSERT INTO claims VALUES (1)", (), "pending claims"),
            ("traffic", "UPDATE slots SET closed=1 WHERE language='en' AND task='stt'", (), "closed slots"),
            ("journal", "INSERT INTO operations VALUES (NULL)", (), "pending operations"),
            ("journal", "UPDATE metadata SET blocked=1", (), "blocked state"),
            ("journal", "INSERT INTO reconciliations VALUES (?)",
             (json.dumps({"reason": PENDING_REASON}),), "pending operations"),
        )
        for index, (store, statement, parameters, problem) in enumerate(cases):
            with self.subTest(statement=statement), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                traffic, journal = root / "traffic.sqlite3", root / "journal.sqlite3"
                with closing(sqlite3.connect(traffic)) as db, db:
                    db.executescript("""
                        CREATE TABLE metadata (version, revision);
                        INSERT INTO metadata VALUES (2, 4);
                        CREATE TABLE claims (token);
                        CREATE TABLE slots (language, task, closed);
                        CREATE TABLE routes (language, task, target);
                    """)
                    db.executemany("INSERT INTO slots VALUES (?,?,0)", [slot for slot, _ in ROUTES])
                    db.executemany("INSERT INTO routes VALUES (?,?,?)", [(*slot, name) for slot, name in ROUTES])
                with closing(sqlite3.connect(journal)) as db, db:
                    db.executescript("""
                        CREATE TABLE metadata (state, blocked);
                        CREATE TABLE operations (outcome);
                        CREATE TABLE reconciliations (outcome);
                    """)
                    db.execute("INSERT INTO metadata VALUES (?,0)", (json.dumps(STATE),))
                for path in (traffic, journal):
                    path.chmod(0o600)
                # Supply the tested file identities on platforms where POSIX mode bits differ.
                paths = {str(traffic): (traffic, (index, 1)), str(journal): (journal, (index, 2))}
                with patch("smoke_speech_runtime.private_file", side_effect=paths.__getitem__):
                    stopped_restart_state(traffic, journal, STATE)
                    path = traffic if store == "traffic" else journal
                    with closing(sqlite3.connect(path)) as db, db:
                        db.execute(statement, parameters)
                    before = (traffic.read_bytes(), journal.read_bytes())
                    controls = Mock()
                    with self.assertRaisesRegex(FixtureError, problem):
                        restart_workers(Mock(), controls, (), (), {"state": STATE}, traffic, journal)
                    self.assertEqual(controls.mock_calls, [])
                    self.assertEqual((traffic.read_bytes(), journal.read_bytes()), before)
