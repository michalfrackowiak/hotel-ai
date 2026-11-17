"""The live-host fixture stops before effects when production health fails."""
from pathlib import Path
import unittest
from unittest.mock import MagicMock, call, patch

from arm64_stage import FixtureError
from smoke_speech_runtime import DockerFixture, stage_server


class Arm64StageTests(unittest.TestCase):
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
