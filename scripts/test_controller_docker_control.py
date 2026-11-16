"""Approved-container controls use mocked Docker, never a real daemon."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.docker_control import ApprovedContainer, ControlUnavailable, DockerControls


APPROVED = ApprovedContainer("fixture", "a" * 64, "sha256:" + "b" * 64, 250000000, 33554432, "fixture-owner")
INFO = {"id": "fixture-daemon", "os_type": "linux"}


def record(state="created", **changes):
    return dict(id=APPROVED.container_id, name="/fixture", image_id=APPROVED.image_id,
                nano_cpus=APPROVED.nano_cpus, memory_bytes=APPROVED.memory_bytes,
                owner=APPROVED.owner, privileged=False, state=state) | changes


def output(value):
    return subprocess.CompletedProcess([], 0, json.dumps(value).encode())


@contextmanager
def controls():
    with patch("speech_controller.docker_control.platform.system", return_value="Linux"), \
         patch("speech_controller.docker_control.os.path.isabs", return_value=True), \
         patch("speech_controller.docker_control.os.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)):
        yield DockerControls("/private/docker.sock", "fixture-daemon", [APPROVED])


class DockerControlTests(unittest.TestCase):
    def test_start_and_stop_use_fixed_endpoint_full_id_and_shared_deadline(self):
        hostile = {key: "secret-override" for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_TLS")}
        for action, before, after in (("start", "created", "running"), ("stop", "running", "exited")):
            with self.subTest(action=action), controls() as control, patch.dict(os.environ, hostile), \
                 patch("speech_controller.docker_control.monotonic", side_effect=[100, 101, 102, 103, 105]), \
                 patch("speech_controller.docker_control.subprocess.run", side_effect=[
                     output(INFO), output(record(before)), output(None), output(record(after))]) as run:
                self.assertEqual(getattr(control, action)("fixture"), after)
                self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [9, 8, 7, 5])
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][:3], ["docker", "--host", "unix:///private/docker.sock"])
                    self.assertFalse(call.kwargs["shell"])
                    self.assertTrue(set(hostile).isdisjoint(call.kwargs["env"]))
                    self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
                mutation = run.call_args_list[2]
                expected = ["start", APPROVED.container_id] if action == "start" else ["stop", "--time", "3", APPROVED.container_id]
                self.assertEqual(mutation.args[0][3:], expected)
                self.assertEqual(mutation.kwargs["stdout"], subprocess.DEVNULL)
                for call in (run.call_args_list[1], run.call_args_list[3]):
                    self.assertEqual(call.args[0][-1], APPROVED.container_id)
                    self.assertNotIn("{{json .}}", call.args[0])

    def test_idempotent_states_do_not_mutate(self):
        for action, state in (("start", "running"), ("stop", "created"), ("stop", "exited")):
            with self.subTest(action=action, state=state), controls() as control, \
                 patch("speech_controller.docker_control.subprocess.run", side_effect=[output(INFO), output(record(state))]) as run:
                self.assertEqual(getattr(control, action)("fixture"), state)
                self.assertEqual(run.call_count, 2)

    def test_mismatched_identity_or_container_never_mutates(self):
        with controls() as control, patch("speech_controller.docker_control.subprocess.run", return_value=output(INFO | {"id": "other"})) as run:
            with self.assertRaises(ControlUnavailable):
                control.start("fixture")
            self.assertEqual(run.call_count, 1)
        changes = ({"id": "c" * 64}, {"name": "/other"}, {"image_id": "sha256:" + "c" * 64},
                   {"nano_cpus": 1}, {"memory_bytes": 1}, {"owner": None}, {"owner": "other"},
                   {"privileged": True}, {"privileged": 0}, {"nano_cpus": True}, {"extra": "secret"})
        for change in (*changes, *({"state": state} for state in ("paused", "restarting", "removing", "dead"))):
            with self.subTest(change=change), controls() as control, \
                 patch("speech_controller.docker_control.subprocess.run", side_effect=[output(INFO), output(record(**change))]) as run:
                with self.assertRaises(ControlUnavailable):
                    control.start("fixture")
                self.assertEqual(run.call_count, 2)

    def test_uncertain_mutation_and_postcondition_failure_never_retry(self):
        for third in (subprocess.TimeoutExpired("secret", 3), subprocess.CalledProcessError(1, "secret")):
            with self.subTest(third=third), controls() as control, \
                 patch("speech_controller.docker_control.subprocess.run", side_effect=[output(INFO), output(record()), third]) as run:
                with self.assertRaises(ControlUnavailable) as caught:
                    control.start("fixture")
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(run.call_count, 3)
        for post in (record("exited"), record("running", owner="other")):
            with self.subTest(post=post), controls() as control, \
                 patch("speech_controller.docker_control.subprocess.run", side_effect=[output(INFO), output(record()), output(None), output(post)]) as run:
                with self.assertRaises(ControlUnavailable):
                    control.start("fixture")
                self.assertEqual(run.call_count, 4)
        with controls() as control, patch("speech_controller.docker_control.monotonic", side_effect=[100, 101, 102, 111]), \
             patch("speech_controller.docker_control.subprocess.run", side_effect=[output(INFO), output(record())]) as run:
            with self.assertRaises(ControlUnavailable):
                control.start("fixture")
            self.assertEqual(run.call_count, 2)

    def test_invalid_approval_endpoint_and_unknown_name_fail_closed(self):
        for change in ({"name": "--help"}, {"container_id": "short"}, {"image_id": "image:latest"},
                       {"nano_cpus": True}, {"memory_bytes": 0}, {"owner": "bad;command"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(APPROVED, **change)
        with self.assertRaises(FrozenInstanceError):
            APPROVED.name = "other"
        with controls() as control, patch("speech_controller.docker_control.subprocess.run") as run:
            with self.assertRaises(ControlUnavailable):
                control.start("unknown")
            run.assert_not_called()
            for mode in (stat.S_IFLNK, stat.S_IFREG):
                with patch("speech_controller.docker_control.os.lstat", return_value=SimpleNamespace(st_mode=mode)), self.assertRaises(ControlUnavailable):
                    control.start("fixture")
            with patch("speech_controller.docker_control.platform.system", return_value="Windows"), self.assertRaises(ControlUnavailable):
                control.start("fixture")
            run.assert_not_called()

    def test_malformed_or_missing_docker_is_redacted(self):
        invalid = [b"secret invalid", b"[]", b"x" * 4097, b"[" * 2000 + b"]" * 2000,
                   json.dumps(INFO).replace('"id":', '"id":"wrong","id":').encode()]
        for raw in invalid:
            with self.subTest(raw=raw[:20]), controls() as control, \
                 patch("speech_controller.docker_control.subprocess.run", return_value=subprocess.CompletedProcess([], 0, raw)):
                with self.assertRaises(ControlUnavailable) as caught:
                    control.start("fixture")
                self.assertNotIn("secret", str(caught.exception))
        with controls() as control, patch("speech_controller.docker_control.subprocess.run", side_effect=FileNotFoundError("secret")):
            with self.assertRaises(ControlUnavailable):
                control.stop("fixture")


if __name__ == "__main__":
    unittest.main()
