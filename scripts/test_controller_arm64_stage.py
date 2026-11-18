"""The live-host fixture stops before effects when production health fails."""
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, call, patch

from arm64_stage import FixtureError, routed_production_snapshot
from smoke_speech_runtime import DockerFixture, stage_server


class Arm64StageTests(unittest.TestCase):
    def test_routed_live_caps_limit_combined_overlap(self):
        fixture = DockerFixture("/run/docker.sock", source=Path("/tmp/source"))
        with (patch.object(fixture, "run", return_value="fixture-daemon"),
              patch.object(fixture, "cleanup"),
              patch("arm64_stage.sample_health", return_value=0.01),
              patch("smoke_speech_runtime.routed_production_snapshot", return_value={}),
              patch("smoke_speech_runtime.stage") as stage):
            stage_server(fixture, "/run/docker.sock", Path("/tmp/data"), routed_live=True)
        stage.assert_called_once()
        self.assertLessEqual(1.05 + 3 * fixture.worker_cpu / 1_000_000_000 + float(fixture.web_cpu), 2.0)

    def test_routed_snapshot_requires_expected_running_worker_cpu_cap(self):
        docker = MagicMock()
        docker.run.side_effect = [
            json.dumps({"id": "a" * 64, "image": "sha256:" + "b" * 64, "started": "fixture-time",
                        "running": True, "health": "healthy", "project": "hotel-ai", "service": "web",
                        "status": "running", "cpu": 250_000_000}),
            json.dumps({"id": "c" * 64, "image": "sha256:" + "d" * 64, "started": "fixture-time",
                        "running": True, "health": None, "project": "hotel-ai", "service": "routed-piper-primary",
                        "status": "running", "cpu": 500_000_000}),
        ]
        with self.assertRaisesRegex(FixtureError, "worker limits"):
            routed_production_snapshot(docker)
        self.assertEqual(docker.run.call_count, 2)

    def test_failed_health_preflight_does_not_start_stage(self):
        fixture = DockerFixture("/run/docker.sock", source=Path("/tmp/source"))
        with (patch.object(fixture, "run", return_value="fixture-daemon") as docker,
              patch("smoke_speech_runtime.production_snapshot", return_value={}),
              patch("arm64_stage.sample_health", return_value=None),
              patch("smoke_speech_runtime.stage") as stage):
            with self.assertRaisesRegex(FixtureError, "preflight"):
                stage_server(fixture, "/run/docker.sock", Path("/tmp/data"))
        stage.assert_not_called()
        self.assertEqual(docker.call_args_list, [call("info", "--format", "{{.ID}}")])

    def test_health_failure_removes_only_fixture_web_before_waiting_for_attach(self):
        fixture = DockerFixture("/run/docker.sock", source=Path("/tmp/source"))
        fixture.daemon_id = "fixture-daemon"
        web, worker = "a" * 64, "b" * 64
        fixture.containers.update((web, worker))
        process = MagicMock()
        process.poll.return_value = None
        manager = MagicMock()

        def fail_after_attach():
            fixture.health.abort.set()
            return process

        manager.__enter__.side_effect = fail_after_attach
        with (patch.object(fixture, "run", return_value="fixture-daemon") as docker,
              patch("smoke_speech_runtime.subprocess.Popen", return_value=manager)):
            with self.assertRaisesRegex(FixtureError, "aborted"):
                fixture.attach_admin(web)
        self.assertEqual(docker.call_args_list, [call("info", "--format", "{{.ID}}"),
                                                call("rm", "--force", web)])
        self.assertEqual(fixture.containers, {worker})
        process.wait.assert_called_once_with(timeout=5)
