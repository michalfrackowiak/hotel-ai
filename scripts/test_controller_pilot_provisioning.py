"""Verify the one-off preparation command without access to a daemon or accounts."""
from contextlib import nullcontext
from ipaddress import ip_network
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


if sys.platform.startswith("linux"):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import provision_speech_pilot as pilot


@unittest.skipUnless(sys.platform.startswith("linux"), "Native Linux account discovery")
class PilotPreparationTests(unittest.TestCase):
    def test_identity_resume_rejects_existing_private_configuration(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            base.chmod(0o755)
            operator = base / "operator"
            operator.mkdir(mode=0o700)
            original_lstat = type(base).lstat

            def root_owned(path):
                return SimpleNamespace(st_mode=original_lstat(path).st_mode, st_uid=0, st_gid=0)

            with patch.object(pilot, "BASE", base), patch.object(type(base), "lstat", root_owned), \
                    patch.object(pilot, "verify_identity") as identity:
                pilot.verify_resume_identity()
                (operator / "routed.env").touch()
                with self.assertRaisesRegex(pilot.FixtureError, "unexpected state"):
                    pilot.verify_resume_identity()
                identity.assert_called_once_with()

    def test_invalid_compose_leaves_all_resources_unwritten(self):
        options = {
            "verify_release": Mock(),
            "verify_no_conflicts": Mock(),
            "rollback_snapshot": Mock(return_value={}),
            "host_capacity": Mock(return_value=({}, {"piper": "cached-piper", "vosk": "cached-vosk"}, {})),
            "verify_compose": Mock(side_effect=pilot.FixtureError("invalid overlay")),
            "reserve_paths": Mock(),
            "write_private": Mock(),
            "command": Mock(),
        }
        with patch.multiple(pilot, **options):
            with self.assertRaisesRegex(pilot.FixtureError, "invalid overlay"):
                pilot.prepare(ip_network("10.91.84.0/28"))
        for name in ("reserve_paths", "write_private", "command"):
            options[name].assert_not_called()

    def test_mutating_compose_command_targets_only_the_four_stopped_workers(self):
        docker = Mock()
        docker.run.return_value = "created"
        health = Mock()
        health.monitor.return_value = nullcontext()
        operator = Path("/var/lib/hotel-ai-speech/operator")
        commands = Mock()
        with patch("builtins.print"), patch.multiple(
            pilot, LocalDocker=Mock(return_value=docker), ProductionHealth=Mock(return_value=health),
            verify_release=Mock(), verify_no_conflicts=Mock(), rollback_snapshot=Mock(return_value={}),
            host_capacity=Mock(return_value=({}, {"piper": "cached-piper", "vosk": "cached-vosk"}, {})),
            verify_compose=Mock(), reserve_paths=Mock(return_value=operator), write_private=Mock(),
            manifest=Mock(return_value={}), command=commands,
        ):
            pilot.prepare(ip_network("10.91.84.0/28"))
        creation, initialize, startup_check = commands.call_args_list
        self.assertEqual(creation.args[1:], (
            "/usr/bin/docker", "--host", "unix:///run/docker.sock", "compose",
            "--project-name", "hotel-ai", "--env-file", str(operator / "routed.env"),
            "-f", "compose.yaml", "-f", "compose.server.yaml", "-f", "compose.speech-routed.yaml",
            "--profile", "routed-primary", "--profile", "routed-standby",
            "create", "--no-build", "--pull", "never", "--no-recreate",
            "routed-piper-primary", "routed-vosk-primary", "routed-piper-standby", "routed-vosk-standby",
        ))
        self.assertEqual(set(creation.kwargs["env"]), set(pilot.ENV) | {"DJANGO_SECRET_KEY"})
        self.assertIn("speech_controller.initialize", initialize.args)
        self.assertIn("speech_controller.runtime", startup_check.args)
        self.assertEqual(startup_check.args[-1], "--check")


if __name__ == "__main__":
    unittest.main()
