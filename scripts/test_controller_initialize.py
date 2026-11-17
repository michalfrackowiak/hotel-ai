"""Real private SQLite provisioning with mocked Docker observations."""
import json
import os
from pathlib import Path
import socket
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.docker_control import ControlUnavailable, DockerControls
from speech_controller.initialize import initialize, main
from speech_controller.runtime import open_runtime
from speech_controller.runtime_config import RuntimeConfiguration, private_new_store


TOKEN = "fictional-initializer-token-" + "x" * 32
IMAGE_ID = "sha256:" + "a" * 64
NETWORK_ID = "f" * 64


@unittest.skipUnless(sys.platform.startswith("linux"), "POSIX private store and socket semantics")
class InitializeTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        shared = root / "traffic"
        shared.mkdir(mode=0o700)
        self.traffic = shared / "traffic.sqlite3"
        self.journal = root / "lifecycle.sqlite3"
        self.manifest = root / "controller.json"
        self.socket = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.socket.close)
        socket_path = root / "docker.sock"
        self.socket.bind(str(socket_path))
        self.configuration = {
            "version": 1, "actor": "fixture-controller", "port": 8093, "daemon_id": "fixture-daemon",
            "docker_socket": str(socket_path), "traffic_path": str(self.traffic), "journal_path": str(self.journal),
            "policy": {
                "storage_path": str(root), "topology_confirmed": True, "cpu_budget_nanos": 2000000000,
                "memory_budget_bytes": 1024**3, "memory_reserve_bytes": 1024**3,
                "disk_reserve_bytes": 1024**3, "artifact_bytes": [[IMAGE_ID, 1024**2]],
            },
            "deployments": [
                {
                    "name": f"{task}-{role}", "container_id": str(index) * 64, "image_id": IMAGE_ID,
                    "owner": "fixture-owner", "nano_cpus": 400000000, "memory_bytes": 128 * 1024**2,
                    "task": task, "languages": ["en", "pl"], "network_id": NETWORK_ID,
                    "address": f"10.30.0.{index}", "base_url": f"http://10.30.0.{index}:5000",
                }
                for index, (task, role) in enumerate(
                    (("stt", "primary"), ("tts", "primary"), ("stt", "standby"), ("tts", "standby")), 2
                )
            ],
        }
        self.write_manifest(self.configuration)

    def write_manifest(self, value):
        self.manifest.write_text(json.dumps(value))
        self.manifest.chmod(0o600)

    def provision(self, **choices):
        routes = {"primary_stt": "stt-primary", "primary_tts": "tts-primary"} | choices
        initialize(str(self.manifest), TOKEN, **routes)

    def test_provisioned_pair_reopens_at_revision_zero_without_worker_effects(self):
        with self.assertRaises(ValueError):
            RuntimeConfiguration.load(str(self.manifest), TOKEN)
        with (
            patch.object(DockerControls, "verify_stopped_endpoint", return_value=True) as verify,
            patch.object(DockerControls, "start") as start,
            patch.object(DockerControls, "stop") as stop,
        ):
            self.provision()
        for worker in self.configuration["deployments"]:
            verify.assert_any_call(worker["name"], NETWORK_ID, worker["address"])
        self.assertEqual(verify.call_count, 4)
        start.assert_not_called()
        stop.assert_not_called()
        for store in self.traffic, self.journal:
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
        before = self.traffic.read_bytes(), self.journal.read_bytes()
        config = RuntimeConfiguration.load(str(self.manifest), TOKEN)
        with patch.object(DockerControls, "inspect", return_value="created"), open_runtime(config) as executor:
            self.assertEqual(executor.state, executor.observed_state)
            self.assertEqual(executor.state.revision, 0)
            self.assertEqual(dict(executor.state.routes), {
                ("en", "stt"): "stt-primary", ("pl", "stt"): "stt-primary",
                ("en", "tts"): "tts-primary", ("pl", "tts"): "tts-primary",
            })
            self.assertFalse(executor.recovery_status()["blocked"])
        with self.assertRaises(ValueError):
            self.provision()
        self.assertEqual((self.traffic.read_bytes(), self.journal.read_bytes()), before)

    def test_preflight_rejects_bad_targets_or_failed_worker_checks_before_writes(self):
        with patch.object(DockerControls, "verify_stopped_endpoint") as verify:
            with self.assertRaises(ValueError):
                self.provision(primary_stt="tts-primary")
            verify.assert_not_called()
        workers = self.configuration["deployments"]
        duplicate = [*workers[:-1], workers[-1] | {"address": workers[0]["address"],
                                                    "base_url": f"http://{workers[0]['address']}:5001"}]
        self.write_manifest(self.configuration | {"deployments": duplicate})
        with patch.object(DockerControls, "verify_stopped_endpoint") as verify:
            with self.assertRaises(ValueError):
                self.provision()
            verify.assert_not_called()
        self.write_manifest(self.configuration)
        with patch.object(DockerControls, "verify_stopped_endpoint", side_effect=[True, True, True, ControlUnavailable]):
            with self.assertRaises(ControlUnavailable):
                self.provision()
        self.assertFalse(self.traffic.exists())
        self.assertFalse(self.journal.exists())

    def test_existing_store_sidecar_and_socket_in_shared_tree_are_rejected(self):
        self.journal.write_bytes(b"existing journal")
        with self.assertRaises(ValueError):
            self.provision()
        self.assertEqual(self.journal.read_bytes(), b"existing journal")
        self.journal.unlink()
        sidecar = self.traffic.with_suffix(".sqlite3-wal")
        sidecar.symlink_to(self.traffic)
        with self.assertRaises(ValueError):
            self.provision()
        sidecar.unlink()
        with socket.socket(socket.AF_UNIX) as exposed:
            exposed_path = self.traffic.parent / "docker.sock"
            exposed.bind(str(exposed_path))
            self.write_manifest(self.configuration | {"docker_socket": str(exposed_path)})
            with self.assertRaises(ValueError):
                self.provision()
        self.assertFalse(self.traffic.exists())
        self.assertFalse(self.journal.exists())

    def test_interrupted_creation_preserves_reserved_file_without_automatic_retry(self):
        with (
            patch.object(DockerControls, "verify_stopped_endpoint", return_value=True),
            patch("speech_controller.initialize.initialize_routing", side_effect=OSError),
        ):
            with self.assertRaises(OSError):
                self.provision()
        self.assertFalse(self.traffic.exists())
        self.assertEqual(self.journal.read_bytes(), b"")
        with self.assertRaises(ValueError):
            self.provision()
        self.assertEqual(self.journal.read_bytes(), b"")

    def test_journal_appearing_after_preflight_is_preserved(self):
        def late_journal(path):
            destination = private_new_store(path)
            if destination == self.journal:
                destination.write_bytes(b"other initializer")
            return destination

        with (
            patch.object(DockerControls, "verify_stopped_endpoint", return_value=True),
            patch("speech_controller.initialize.private_new_store", side_effect=late_journal),
            self.assertRaises(FileExistsError),
        ):
            self.provision()
        self.assertEqual(self.journal.read_bytes(), b"other initializer")
        self.assertFalse(self.traffic.exists())

    def test_cli_failure_keeps_private_values_out_of_error(self):
        with (
            patch.dict(os.environ, {"HOTEL_SPEECH_CONTROL_TOKEN": TOKEN}),
            patch("speech_controller.initialize.initialize", side_effect=ValueError(TOKEN)),
        ):
            with self.assertRaises(SystemExit) as error:
                main(["--config", str(self.manifest), "--primary-stt", "stt-primary", "--primary-tts", "tts-primary"])
        self.assertNotIn(TOKEN, str(error.exception))
