"""Focused systemd helper checks with private fictional deployment data."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from speech_controller import pilot_service as pilot
from speech_controller.lifecycle import State


ROUTES = ((("en", "stt"), "stt-primary"), (("en", "tts"), "tts-primary"),
          (("pl", "stt"), "stt-primary"), (("pl", "tts"), "tts-primary"))
STATUS_STATE = dict(revision=1, routes=ROUTES)
STATUS = dict(state=STATUS_STATE, observed=STATUS_STATE, blocked=False, pending_requests=[], pending_recoveries=[])
APPROVAL = pilot.WebApproval("fixture-daemon", "a" * 64, "sha256:" + "b" * 64, "fixture-data", "/fixture/traffic")


def web_snapshot():
    return {
        "id": APPROVAL.container_id,
        "image": APPROVAL.image_id,
        "state": "exited",
        "restart": "no",
        "network": "host",
        "bindings": {},
        "publish_all": False,
        "privileged": False,
        "user": "10001:10001",
        "mounts": [
            {"type": "volume", "name": APPROVAL.booking_volume, "destination": "/app/data", "rw": True},
            {"type": "bind", "source": APPROVAL.traffic_dir, "destination": "/run/hotel-ai-traffic", "rw": True},
        ],
    }


class PilotReadinessTests(unittest.TestCase):
    def test_ready_status_requires_exact_revision_routes_and_no_uncertainty(self):
        expected = json.dumps(STATUS_STATE, sort_keys=True)
        self.assertTrue(pilot.ready_status(STATUS, expected))
        for delta in (
            {"state": STATUS_STATE | {"revision": True}},
            {"observed": STATUS_STATE | {"revision": 2}},
            {"state": STATUS_STATE | {"routes": list(reversed(ROUTES))}},
            {"pending_requests": ["pending"]},
            {"pending_recoveries": ["pending"]},
            {"blocked": True},
        ):
            with self.subTest(delta=delta):
                self.assertFalse(pilot.ready_status(STATUS | delta, expected))

    def test_wait_requires_authenticated_loopback_and_durable_state_agreement(self):
        token = "fixture-token" * 3
        config = SimpleNamespace(control=SimpleNamespace(port=19093, token=token), traffic_path="fixture", targets=())
        response = SimpleNamespace(stdout=json.dumps(STATUS).encode())
        with (patch.object(pilot.RuntimeConfiguration, "load", return_value=config),
              patch.object(pilot, "RoutedTrafficGate", return_value=SimpleNamespace(state=State(1, ROUTES))),
              patch.object(pilot.subprocess, "run", return_value=response) as fetch,
              patch.object(pilot, "monotonic", side_effect=(0, 1, 2))):
            pilot.wait_controller("fixture-manifest", token)
        self.assertNotIn(token, fetch.call_args.args[0])
        self.assertEqual(json.loads(fetch.call_args.kwargs["input"]), {
            "url": "http://127.0.0.1:19093/v1/status", "token": token,
        })

    def test_wait_deadline_does_not_accept_failed_authentication(self):
        config = SimpleNamespace(control=SimpleNamespace(port=19093, token="fixture-token" * 3),
                                 traffic_path="fixture", targets=())
        failure = subprocess.CalledProcessError(4, ["fictional-status-worker"])
        with (patch.object(pilot.RuntimeConfiguration, "load", return_value=config),
              patch.object(pilot.subprocess, "run", side_effect=failure),
              patch.object(pilot, "monotonic", side_effect=(0, 119, 120, 120)),
              patch.object(pilot, "sleep")):
            with self.assertRaises(TimeoutError):
                pilot.wait_controller("fixture-manifest", config.control.token)


class PilotWebTests(unittest.TestCase):
    def test_web_refuses_unsafe_or_unstopped_container_before_launch(self):
        for delta in ({"restart": "unless-stopped"}, {"state": "running"}, {"network": "bridge"},
                      {"mounts": []}, {"publish_all": True}, {"privileged": True}, {"user": "0:0"}):
            with (self.subTest(delta=delta),
                  patch.object(pilot.WebApproval, "load", return_value=APPROVAL),
                  patch.object(pilot, "web_state", return_value=web_snapshot() | delta),
                  patch.object(pilot.os, "execve") as launch):
                with self.assertRaises(ValueError):
                    pilot.run_web("web-run", "fixture-approval")
                launch.assert_not_called()

    def test_web_refuses_another_running_booking_writer(self):
        with patch.object(pilot, "docker_output", return_value=b"other-fixture-writer\n"):
            with self.assertRaisesRegex(ValueError, "booking volume open"):
                pilot.check_web_offline(APPROVAL, web_snapshot())

    def test_start_and_stop_use_only_the_exact_approved_identity(self):
        with (TemporaryDirectory() as directory,
              patch.object(pilot, "WEB_OWNER", Path(directory) / "attached"),
              patch.object(pilot.WebApproval, "load", return_value=APPROVAL),
              patch.object(pilot, "web_state", side_effect=(web_snapshot(), web_snapshot() | {"state": "running"})),
              patch.object(pilot, "docker_output", return_value=b""),
              patch.object(pilot.os, "execve") as launch,
              patch.object(pilot.subprocess, "run") as stop):
            pilot.run_web("web-stop", "fixture-approval")
            stop.assert_not_called()
            pilot.run_web("web-run", "fixture-approval")
            pilot.run_web("web-stop", "fixture-approval")
            self.assertFalse(pilot.WEB_OWNER.exists())
        launch.assert_called_once_with(pilot.DOCKER[0], [*pilot.DOCKER, "start", "--attach", APPROVAL.container_id],
                                       pilot.DOCKER_ENV)
        self.assertEqual(stop.call_args.args[0], [*pilot.DOCKER, "stop", "--time", "30", APPROVAL.container_id])


if __name__ == "__main__":
    unittest.main()
