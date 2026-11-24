"""Offline capture verification against real routed stores and fictional workers."""

from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.journal import encode
from speech_controller.lifecycle import SwitchRequest
from speech_controller.recovery_evidence import EvidenceRejected, main, validate_evidence
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routed_recovery import PENDING_REASON
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from test_controller_routed_executor import Controls, Crash, DEPLOYMENTS, EN, ROUTES, TARGETS


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.traffic, self.journal, self.capture = (
            root / name for name in ("traffic.sqlite3", "journal.sqlite3", "capture.json")
        )
        initialize_routing(self.traffic, TARGETS, ROUTES)
        self.gate = RoutedTrafficGate(self.traffic, TARGETS)
        _, device, inode, fingerprint = self.gate.binding
        prior_path = "/fictional/source/traffic.sqlite3"
        controls = Controls()
        backend = DockerLifecycleBackend(controls, DEPLOYMENTS,
            admission=lambda *args: True, traffic=self.gate, readiness=lambda *args: True,
            reconciliation=lambda *args: True)
        with patch.object(RoutedTrafficGate, "binding", new_callable=PropertyMock,
                          return_value=(prior_path, device, inode, fingerprint)):
            executor = RoutedLifecycleExecutor(backend, DEPLOYMENTS, ROUTES, self.journal)
        self.assertEqual(executor.execute(SwitchRequest("first-switch", 0, EN, "new")).status, "succeeded")
        with patch.object(executor, "_run_pending", side_effect=Crash):
            with self.assertRaises(Crash):
                executor.execute(SwitchRequest("interrupted", 1, EN, "old"))
        self.assertEqual(executor.recover("recovery", 1, self.gate.state, "Fixture operator",
                                         "Verified fictional workers").status, "reconciled")
        self.state = executor.state
        self.proof = dict(source_traffic_path=prior_path, source_traffic_device=device,
                          source_traffic_inode=inode, targets=[asdict(target) for target in TARGETS])
        self.write_capture()

    def write_capture(self, **changes):
        value = self.proof | {
            "traffic_sha256": hashlib.sha256(self.traffic.read_bytes()).hexdigest(),
            "journal_sha256": hashlib.sha256(self.journal.read_bytes()).hexdigest(),
        } | changes
        self.capture.write_text(encode(value), encoding="utf-8")

    def query(self, path, sql, parameters=()):
        with closing(sqlite3.connect(path)) as db:
            db.execute(sql, parameters)
            db.commit()

    def validate(self):
        return validate_evidence(self.traffic, self.journal, self.capture)

    def test_verifies_mixed_revision_and_history_without_touching_artifacts(self):
        before = {path.name: path.read_bytes() for path in self.capture.parent.iterdir()}
        self.assertEqual(self.validate(), {
            "state": asdict(self.state), "completed_switches": 2, "completed_recoveries": 1,
        })
        self.assertEqual(self.state.revision, 1)
        self.assertEqual(dict(self.state.routes)[EN], "new")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.capture.parent.iterdir()})

    def test_refuses_wrong_checksums_or_prior_binding(self):
        for change, message in (
            ({"journal_sha256": "0" * 64}, "checksum"),
            ({"source_traffic_path": "/wrong/traffic.sqlite3"}, "binding"),
            ({"source_traffic_inode": self.proof["source_traffic_inode"] + 1}, "binding"),
            ({"source_traffic_path": "/invalid/../traffic.sqlite3"}, "canonical"),
        ):
            with self.subTest(change=change):
                self.write_capture(**change)
                with self.assertRaisesRegex(EvidenceRejected, message):
                    self.validate()

    def test_refuses_unsettled_or_incompatible_copies_even_with_matching_hashes(self):
        original = {path: path.read_bytes() for path in (self.traffic, self.journal)}
        pending = encode(dict(status="recovery_required", reason=PENDING_REASON, state=asdict(self.state)))
        cases = (
            (self.traffic, "UPDATE slots SET closed=1", (), "closed"),
            (self.traffic, "INSERT INTO claims VALUES ('claim','en','tts')", (), "claims"),
            (self.traffic, "UPDATE metadata SET revision=2", (), "disagree"),
            (self.journal, "UPDATE metadata SET blocked=1", (), "blocked"),
            (self.journal, "UPDATE operations SET outcome=NULL", (), "pending switch"),
            (self.journal, "UPDATE reconciliations SET outcome=?", (pending,), "pending"),
            (self.journal, "UPDATE operations SET outcome='{}'", (), "decoded"),
            (self.journal, "ALTER TABLE operations ADD COLUMN spare TEXT", (), "schema"),
        )
        for path, sql, parameters, message in cases:
            with self.subTest(sql=sql):
                path.write_bytes(original[path])
                self.query(path, sql, parameters)
                self.write_capture()
                before = tuple(item.read_bytes() for item in original)
                with self.assertRaisesRegex(EvidenceRejected, message):
                    self.validate()
                self.assertEqual(before, tuple(item.read_bytes() for item in original))
                path.write_bytes(original[path])

    def test_refuses_sqlite_sidecars_beside_captured_database(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.traffic) + suffix)
                sidecar.touch()
                try:
                    with self.assertRaisesRegex(EvidenceRejected, "sidecar"):
                        self.validate()
                finally:
                    sidecar.unlink()

    def test_cli_rejection_reports_no_private_capture_content(self):
        self.write_capture(journal_sha256="0" * 64)
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["--traffic", str(self.traffic), "--journal", str(self.journal), "--capture", str(self.capture)])
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("checksum", errors.getvalue())
        self.assertNotIn(str(self.capture.parent), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
