"""Lifecycle integration tests use a recording backend, never Docker or guests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
from threading import Event
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.lifecycle import Deployment, LifecycleExecutor, SLOTS, SwitchRequest


DEPLOYMENTS = (
    Deployment("shared-stt", "stt", ("en", "pl")),
    Deployment("shared-tts", "tts", ("en", "pl")),
    Deployment("new-stt", "stt", ("en", "pl")),
    Deployment("english-stt", "stt", ("en",)),
    Deployment("polish-tts", "tts", ("pl",)),
)
ROUTES = {slot: "shared-" + slot[1] for slot in SLOTS}


class Backend:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.not_ready = set()
        self.admitted = True

    def call(self, method, value):
        self.calls.append((method, value))
        if (method, value) in self.fail:
            raise RuntimeError("secret backend diagnostic")

    def revalidate(self, current, destination):
        self.call("revalidate", destination.revision)
        return self.admitted

    def drain(self, slot):
        self.call("drain", slot)

    def start(self, name):
        self.call("start", name)

    def stop(self, name):
        self.call("stop", name)

    def ready(self, name, slot):
        self.call("ready", (name, slot))
        return name not in self.not_ready

    def resume(self, slot):
        self.call("resume", slot)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.executor = LifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES)

    def request(self, request_id="one", revision=0, slot=("en", "stt"), target="new-stt"):
        return SwitchRequest(request_id, revision, slot, target)

    def test_shared_worker_retained_until_last_language_moves(self):
        first = self.executor.execute(self.request())
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(self.backend.calls, [
            ("revalidate", 1), ("drain", ("en", "stt")), ("revalidate", 1), ("start", "new-stt"),
            ("ready", ("new-stt", ("en", "stt"))), ("resume", ("en", "stt")),
        ])
        self.assertEqual(len(set(dict(first.state.routes).values())), 3)
        self.backend.calls.clear()
        second = self.executor.execute(self.request("two", 1, ("pl", "stt")))
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.state.revision, 2)
        self.assertEqual(self.backend.calls, [
            ("revalidate", 2), ("drain", ("pl", "stt")), ("revalidate", 2), ("ready", ("new-stt", ("pl", "stt"))),
            ("stop", "shared-stt"), ("resume", ("pl", "stt")),
        ])
        self.assertEqual(len(set(dict(second.state.routes).values())), 2)
        self.executor.execute(self.request("three", 2, ("en", "stt"), "english-stt"))
        self.executor.execute(self.request("four", 3, ("pl", "tts"), "polish-tts"))
        self.assertEqual(len(set(dict(self.executor.state.routes).values())), 4)

    def test_idempotent_results_conflict_stale_and_noop(self):
        request = self.request()
        result = self.executor.execute(request)
        calls = list(self.backend.calls)
        self.assertIs(self.executor.execute(request), result)
        self.assertEqual(self.executor.execute(self.request(target="english-stt")).reason, "request_id_conflict")
        self.assertEqual(self.executor.execute(self.request("stale")).reason, "stale_revision")
        unchanged = self.executor.execute(self.request("noop", 1))
        self.assertEqual((unchanged.status, unchanged.state.revision), ("unchanged", 1))
        self.assertEqual(self.backend.calls, calls)
        with self.assertRaises(FrozenInstanceError):
            result.state.revision = 100

    def test_invalid_targets_admission_and_full_journal_do_not_mutate(self):
        for target, slot in (("unknown", ("en", "stt")), ("english-stt", ("pl", "stt")),
                             ("shared-tts", ("en", "stt"))):
            result = self.executor.execute(self.request(target + str(slot[0]), slot=slot, target=target))
            self.assertEqual(result.reason, "unapproved_or_incompatible_target")
        self.assertFalse(self.backend.calls)
        self.backend.admitted = False
        self.assertEqual(self.executor.execute(self.request()).reason, "admission_denied")
        self.assertEqual(self.backend.calls, [("revalidate", 1)])
        self.backend.fail.add(("revalidate", 1))
        self.assertEqual(self.executor.execute(self.request("unavailable")).reason, "admission_unavailable")
        executor = LifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES, journal_limit=1)
        first = self.request(target="shared-stt")
        result = executor.execute(first)
        self.assertEqual(executor.execute(self.request("next")).reason, "journal_full")
        self.assertIs(executor.execute(first), result)

    def test_readiness_and_partial_start_failures_roll_back(self):
        for failure in ("ready", "start", "drain"):
            with self.subTest(failure=failure):
                backend = Backend()
                executor = LifecycleExecutor(backend, DEPLOYMENTS, ROUTES)
                if failure == "ready":
                    backend.not_ready.add("new-stt")
                else:
                    backend.fail.add((failure, "new-stt" if failure == "start" else ("en", "stt")))
                result = executor.execute(self.request())
                self.assertEqual(result.status, "rolled_back")
                self.assertEqual(result.state.revision, 0)
                self.assertEqual(dict(result.state.routes), ROUTES)
                self.assertEqual(("stop", "new-stt") in backend.calls, failure != "drain")
                self.assertEqual(backend.calls[-2:], [("ready", ("shared-stt", ("en", "stt"))), ("resume", ("en", "stt"))])
                self.assertNotIn("secret", str(result))

    def test_rollback_failures_latch_closed(self):
        for failure in ("cleanup", "old_ready", "resume"):
            with self.subTest(failure=failure):
                backend = Backend()
                executor = LifecycleExecutor(backend, DEPLOYMENTS, ROUTES)
                backend.not_ready.add("new-stt")
                if failure == "cleanup":
                    backend.fail.add(("stop", "new-stt"))
                elif failure == "old_ready":
                    backend.not_ready.add("shared-stt")
                else:
                    backend.fail.add(("resume", ("en", "stt")))
                result = executor.execute(self.request())
                self.assertEqual(result.status, "recovery_required")
                self.assertEqual(result.state.revision, 0)
                calls = list(backend.calls)
                self.assertEqual(executor.execute(self.request("retry")).reason, "reconciliation_required")
                self.assertEqual(backend.calls, calls)
                if failure == "old_ready":
                    self.assertNotIn(("resume", ("en", "stt")), calls)

    def test_cleanup_and_resume_failure_after_commit_preserve_new_route(self):
        for failure in ("stop", "resume"):
            with self.subTest(failure=failure):
                backend = Backend()
                routes = ROUTES | {("pl", "stt"): "new-stt"}
                executor = LifecycleExecutor(backend, DEPLOYMENTS, routes)
                backend.fail.add((failure, "shared-stt" if failure == "stop" else ("en", "stt")))
                result = executor.execute(self.request())
                self.assertEqual(result.status, "cleanup_required" if failure == "stop" else "recovery_required")
                self.assertEqual(result.state.revision, 1)
                self.assertEqual(dict(result.state.routes)[("en", "stt")], "new-stt")
                self.assertEqual(executor.execute(self.request("retry", 1)).reason, "reconciliation_required")

    def test_concurrent_switch_is_busy_without_backend_work(self):
        entered, release = Event(), Event()
        original = self.backend.revalidate

        def wait(current, destination):
            entered.set()
            release.wait(2)
            return original(current, destination)

        self.backend.revalidate = wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.executor.execute, self.request())
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(self.executor.execute(self.request("other")).reason, "busy")
                self.assertFalse(self.backend.calls)
            finally:
                release.set()
            self.assertEqual(first.result().status, "succeeded")

    def test_initialization_and_requests_are_validated(self):
        with self.assertRaises(ValueError):
            LifecycleExecutor(self.backend, [object()], ROUTES)
        with self.assertRaises(ValueError):
            LifecycleExecutor(self.backend, DEPLOYMENTS, {("en", "stt"): "shared-stt"})
        with self.assertRaises(ValueError):
            LifecycleExecutor(self.backend, DEPLOYMENTS, ROUTES | {("pl", "stt"): "english-stt"})
        for args in (("bad;command", 0, ("en", "stt"), "new-stt"),
                     ("one", True, ("en", "stt"), "new-stt"), ("one", 0, ("de", "stt"), "new-stt")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SwitchRequest(*args)


if __name__ == "__main__":
    unittest.main()
