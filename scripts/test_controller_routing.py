"""Atomic route/claim publication with fictional private endpoints."""
from contextlib import closing
from dataclasses import asdict, FrozenInstanceError, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.lifecycle import State
from speech_controller.readiness import ProbeTarget
from speech_controller.routing import RoutedTrafficGate, initialize_routing, load_targets
from speech_controller.traffic import TrafficBusy, TrafficGate, TrafficUnavailable, initialize


TARGETS = (ProbeTarget("old", "stt", "http://old:5001", ("en", "pl")),
           ProbeTarget("new", "stt", "http://new:5001", ("en", "pl")),
           ProbeTarget("tts", "tts", "http://tts:5000", ("en", "pl")))
ROUTES = {(lang, task): "old" if task == "stt" else "tts" for lang in ("en", "pl") for task in ("stt", "tts")}
EN = ("en", "stt")
PL = ("pl", "stt")


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "routing.sqlite3"
        initialize_routing(self.path, TARGETS, ROUTES)
        self.gate = RoutedTrafficGate(self.path, TARGETS, timeout=0.3)
        self.current = State(0, tuple(sorted(ROUTES.items())))
        self.next = State(1, tuple(sorted((ROUTES | {EN: "new"}).items())))

    def test_publish_requires_drained_empty_slot_and_keeps_admission_closed(self):
        self.assertFalse(self.gate.publish(self.current, self.next))
        with self.gate.claim(EN) as claim:
            self.assertEqual((claim.target, claim.route_revision), (TARGETS[0], 0))
            with self.assertRaises(AttributeError):
                claim.target = TARGETS[1]
            with self.assertRaises(FrozenInstanceError):
                claim.target.base_url = "http://elsewhere"
            self.assertFalse(self.gate.drain(EN))
            self.assertFalse(self.gate.publish(self.current, self.next))
        self.assertTrue(self.gate.publish(self.current, self.next))
        with self.assertRaises(TrafficBusy):
            with self.gate.claim(EN):
                self.fail("publication reopened admission")
        with self.gate.claim(PL) as claim:
            self.assertEqual((claim.target.name, claim.route_revision), ("old", 1))
        self.assertTrue(self.gate.resume(EN))
        with self.gate.claim(EN) as claim:
            self.assertEqual((claim.target.name, claim.route_revision), ("new", 1))

    def test_stale_revision_conflicting_prior_and_multiple_changes_are_rejected(self):
        self.assertTrue(self.gate.drain(EN))
        bad_states = (State(2, self.next.routes), State(True, self.next.routes), State(2**63, self.next.routes),
                      State(1, tuple(sorted((ROUTES | {EN: "new", PL: "new"}).items()))),
                      State(1, tuple(sorted((ROUTES | {EN: "tts"}).items()))),
                      State(1, self.current.routes))
        for state in bad_states:
            self.assertFalse(self.gate.publish(self.current, state))
        self.assertTrue(self.gate.publish(self.current, self.next))
        self.assertFalse(self.gate.publish(self.current, self.next))
        self.assertEqual(self.gate.state, self.next)

    def test_uncertain_claim_blocks_publication_after_restart(self):
        with self.gate.claim(EN) as claim:
            claim.mark_uncertain()
        restarted = RoutedTrafficGate(self.path, TARGETS, timeout=0.3)
        self.assertFalse(restarted.publish(self.current, self.next))
        self.assertFalse(restarted.resume(EN))
        self.assertEqual(restarted.state, self.current)

    def test_catalog_drift_invalid_stores_and_version_mismatch_fail_closed(self):
        with self.assertRaises(FileExistsError):
            initialize_routing(self.path, TARGETS, ROUTES)
        with self.assertRaises(TrafficUnavailable):
            TrafficGate(self.path)
        drift = (replace(TARGETS[0], base_url="http://different:5001"), *TARGETS[1:])
        with self.assertRaises(TrafficUnavailable):
            RoutedTrafficGate(self.path, drift)
        self.assertEqual(RoutedTrafficGate(self.path, tuple(reversed(TARGETS))).state, self.current)
        legacy = self.path.with_name("legacy.sqlite3")
        initialize(legacy)
        self.assertTrue(TrafficGate(legacy).drain(EN))
        with self.assertRaises(TrafficUnavailable):
            RoutedTrafficGate(legacy, TARGETS)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE routes SET target='missing' WHERE language='en' AND task='stt'")
            db.commit()
        with self.assertRaises(TrafficUnavailable):
            with self.gate.claim(EN):
                self.fail("corrupt route admitted")

    def test_target_json_rejects_untrusted_shape_and_catalog(self):
        raw = json.dumps([asdict(target) for target in TARGETS])
        self.assertEqual(load_targets(raw), TARGETS)
        bad = ("", "{}", "[]", raw[:-1], '[{"name":"x","name":"y"}]',
               json.dumps([asdict(TARGETS[0])] * 2),
               json.dumps([asdict(TARGETS[0]) | {"base_url": "http://user:pass@host"}]),
               json.dumps([asdict(TARGETS[0]) | {"extra": True}]),
               json.dumps([asdict(TARGETS[0]) | {"languages": "en"}]))
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_targets(value)

    def test_publish_and_claim_use_atomic_database_transactions(self):
        self.assertTrue(self.gate.drain(EN))
        real_connect = sqlite3.connect

        def reject_revision(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            db.set_authorizer(lambda action, table, column, *_: sqlite3.SQLITE_DENY
                              if action == sqlite3.SQLITE_UPDATE and table == "metadata" else sqlite3.SQLITE_OK)
            return db

        with patch("speech_controller.traffic.sqlite3.connect", side_effect=reject_revision):
            self.assertFalse(self.gate.publish(self.current, self.next))
        self.assertEqual(self.gate.state, self.current)
        self.assertTrue(self.gate.resume(EN))
        with patch.object(self.gate, "_new_claim", side_effect=ValueError):
            with self.assertRaises(TrafficUnavailable):
                with self.gate.claim(EN):
                    self.fail("failed route selection admitted")
        self.assertTrue(self.gate.drain(EN))
        self.assertTrue(self.gate.publish(self.current, self.next))

    def test_new_process_observes_published_route_while_other_language_is_unchanged(self):
        self.assertTrue(self.gate.drain(EN))
        self.assertTrue(self.gate.publish(self.current, self.next))
        self.assertTrue(self.gate.resume(EN))
        code = (
            "import sys\nfrom speech_controller.routing import RoutedTrafficGate,load_targets\n"
            "gate=RoutedTrafficGate(sys.argv[1],load_targets(sys.stdin.read()))\n"
            "for language in ('en','pl'):\n"
            " with gate.claim((language,'stt')) as claim:\n"
            "  print(language,claim.target.name,claim.route_revision)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                cwd=Path(__file__).resolve().parents[1],
                                input=json.dumps([asdict(target) for target in TARGETS]),
                                text=True, capture_output=True, timeout=5, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.stdout.splitlines(), ["en new 1", "pl old 1"])
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.gate.drain(EN))
        self.assertTrue(self.gate.drain(PL))


if __name__ == "__main__":
    unittest.main()
