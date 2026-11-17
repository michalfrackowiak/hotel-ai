"""Existing real SQLite stores with fictional Docker observations, no real daemon."""
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.admission import ControllerAdmission, ResourcePolicy
from speech_controller.backend import DockerLifecycleBackend
from speech_controller.dispatch import ControlConfiguration
from speech_controller.docker_control import ApprovedContainer, DockerControls
from speech_controller.endpoint_binding import BoundReadiness, EndpointBinding
from speech_controller.lifecycle import SwitchRequest
from speech_controller.readiness import ProbeTarget
from speech_controller.routed_executor import RoutedLifecycleExecutor
from speech_controller.routing import RoutedTrafficGate, initialize_routing
from speech_controller.runtime import RuntimeServer, main, open_runtime
from speech_controller.runtime_config import RuntimeConfiguration, private_file


TOKEN = "fictional-runtime-token-" + "x" * 32


class Crash(BaseException):
    pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.root.chmod(0o700)
        self.approvals = tuple(ApprovedContainer(name, char * 64, "sha256:" + "f" * 64,
            250000000, 33554432, "test-owner") for name, char in (("tts", "a"), ("stt", "b")))
        self.targets = tuple(ProbeTarget(name, name, f"http://10.20.0.{i}:5000", ("en", "pl"))
                             for i, name in enumerate(("tts", "stt"), 2))
        self.bindings = tuple(EndpointBinding(target.name, "c" * 64, f"10.20.0.{i}")
                              for i, target in enumerate(self.targets, 2))
        self.routes = {(language, task): task for language in ("en", "pl") for task in ("tts", "stt")}
        self.traffic_path, self.journal_path = self.root / "traffic.sqlite3", self.root / "journal.sqlite3"
        initialize_routing(self.traffic_path, self.targets, self.routes)
        self.policy = ResourcePolicy("test-daemon", str(self.root), True, 1000000000,
            134217728, 33554432, 33554432, ((self.approvals[0].image_id, 1048576),))
        with patch.object(DockerControls, "_check_socket"):
            control = DockerControls(str(self.root / "docker.sock"), "test-daemon", self.approvals)
            admission = ControllerAdmission(control, self.policy)
            traffic = RoutedTrafficGate(self.traffic_path, self.targets)
            backend = DockerLifecycleBackend(control, traffic.deployments, admission=admission, traffic=traffic,
                readiness=BoundReadiness(control, self.targets, self.bindings),
                reconciliation=admission.verify_reconciliation)
            self.executor = RoutedLifecycleExecutor(backend, traffic.deployments, self.routes, self.journal_path)
        self.journal_path.chmod(0o600)
        identities = tuple((path.stat().st_dev, path.stat().st_ino) for path in (self.traffic_path, self.journal_path))
        self.config = RuntimeConfiguration(ControlConfiguration(TOKEN, "test-service"), self.approvals,
            self.targets, self.bindings, self.policy, str(self.traffic_path), str(self.journal_path),
            str(self.root / "docker.sock"), "test-daemon", identities)

    @contextmanager
    def runtime(self, config=None):
        # Windows cannot enforce POSIX ownership/modes. Linux CI uses real checks.
        def windows_file(value):
            path = Path(value)
            info = path.stat()
            if not path.is_file() or not info.st_size:
                raise ValueError
            return path, (info.st_dev, info.st_ino)
        with patch("speech_controller.runtime.platform.system", return_value="Linux"), \
                patch.object(DockerControls, "_check_socket"), \
                patch.object(DockerControls, "inspect", return_value="running"), \
                patch("speech_controller.runtime.private_file", side_effect=private_file if os.name != "nt" else windows_file):
            with open_runtime(config or self.config) as executor:
                yield executor

    def test_reopen_preserves_stores_and_does_not_probe_or_change_workers(self):
        before = (self.traffic_path.read_bytes(), self.journal_path.read_bytes())
        with patch.object(DockerControls, "start") as start, patch.object(DockerControls, "stop") as stop, \
                patch.object(BoundReadiness, "__call__") as ready, self.runtime() as executor:
            self.assertEqual(executor.state, self.executor.state)
            self.assertFalse(executor.recovery_status()["blocked"])
            start.assert_not_called()
            stop.assert_not_called()
            ready.assert_not_called()
        self.assertEqual(before, (self.traffic_path.read_bytes(), self.journal_path.read_bytes()))

    def test_route_owner_lock_rejects_second_runtime_and_releases_after_failure(self):
        with self.runtime():
            with self.assertRaises(ValueError):
                with self.runtime():
                    self.fail("second owner admitted")
        with self.assertRaises(RuntimeError):
            with self.runtime():
                raise RuntimeError("test")
        with self.runtime():
            pass
        self.assertFalse(RuntimeServer.daemon_threads)

    def test_pending_operation_survives_reopen_without_replaying(self):
        request = SwitchRequest("pending", 0, ("en", "stt"), "stt")
        with patch.object(self.executor, "_run_pending", side_effect=Crash):
            with self.assertRaises(Crash):
                self.executor.execute(request)
        with self.runtime() as executor:
            self.assertEqual(executor.recovery_status()["pending_requests"], ("pending",))
            self.assertEqual(executor.execute(request).reason, "pending_reconciliation")

    def test_route_journal_revision_gap_remains_visible_without_adoption(self):
        # Represents a route publication whose journal acknowledgement was lost.
        with closing(sqlite3.connect(self.traffic_path)) as db:
            db.execute("UPDATE metadata SET revision=1")
            db.commit()
        before = (self.traffic_path.read_bytes(), self.journal_path.read_bytes())
        with self.runtime() as executor:
            self.assertEqual(executor.state.revision, 0)
            self.assertEqual(executor.observed_state.revision, 1)
        self.assertEqual(before, (self.traffic_path.read_bytes(), self.journal_path.read_bytes()))

    def test_server_close_waits_for_dispatched_handler(self):
        entered, release, closed = Event(), Event(), Event()
        answers = []
        original = self.executor.recovery_status
        def blocked_status():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test handler release missed")
            return original()
        with patch.object(self.executor, "recovery_status", side_effect=blocked_status):
            server = RuntimeServer(self.config.control, self.executor, port=0)
            serving = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            serving.start()
            def request():
                connection = HTTPConnection(*server.server_address, timeout=5)
                try:
                    connection.request("GET", "/v1/status", headers={"Authorization": "Bearer " + TOKEN})
                    response = connection.getresponse()
                    answers.append(response.status)
                    response.read()
                finally:
                    connection.close()
            caller = Thread(target=request)
            caller.start()
            def close():
                server.server_close()
                closed.set()
            closing_thread = Thread(target=close)
            try:
                self.assertTrue(entered.wait(3))
                server.shutdown()
                serving.join(2)
                closing_thread.start()
                self.assertFalse(closed.wait(0.1))
            finally:
                release.set()
                server.shutdown()
                serving.join(2)
                caller.join(5)
                if closing_thread.ident is not None:
                    closing_thread.join(5)
                else:
                    server.server_close()
            self.assertTrue(closed.is_set())
            self.assertEqual(answers, [200])

    def test_missing_empty_aliased_and_changed_state_fail_without_initialization(self):
        missing = self.root / "missing.sqlite3"
        empty = self.root / "empty.sqlite3"
        empty.touch(mode=0o600)
        for config in (replace(self.config, journal_path=str(missing)), replace(self.config, journal_path=str(empty)),
                replace(self.config, journal_path=self.config.traffic_path),
                replace(self.config, identities=((0, 0), (0, 1)))):
            with self.subTest(path=config.journal_path), self.assertRaises((ValueError, OSError)):
                with self.runtime(config):
                    self.fail("unsafe state admitted")
        self.assertFalse(missing.exists())
        self.assertEqual(empty.stat().st_size, 0)

    def test_incomplete_journal_and_catalog_drift_are_not_migrated(self):
        with closing(sqlite3.connect(self.journal_path)) as db:
            db.execute("DROP TABLE reconciliations")
            db.commit()
        with self.assertRaises(ValueError):
            with self.runtime():
                self.fail("old journal migrated")
        with closing(sqlite3.connect(self.journal_path)) as db:
            self.assertNotIn("reconciliations", {row[0] for row in db.execute("SELECT name FROM sqlite_master")})
        changed = replace(self.config, targets=(replace(self.targets[0], base_url="http://10.20.0.9:5000"), self.targets[1]))
        with self.assertRaises(Exception):
            with self.runtime(changed):
                self.fail("catalog drift accepted")

    def test_wal_journal_is_rejected_without_changing_storage_mode(self):
        with closing(sqlite3.connect(self.journal_path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        with self.assertRaises(ValueError):
            with self.runtime():
                self.fail("journal mode changed during startup")
        with closing(sqlite3.connect(self.journal_path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_cli_failure_is_generic_and_check_has_no_listener(self):
        with patch("speech_controller.runtime.RuntimeConfiguration.load", side_effect=ValueError(TOKEN)):
            with self.assertRaises(SystemExit) as error:
                main(["--config", str(self.root / "config.json"), "--check"])
            self.assertNotIn(TOKEN, str(error.exception))
        with patch("speech_controller.runtime.RuntimeConfiguration.load", return_value=self.config), \
                patch("speech_controller.runtime.open_runtime", return_value=self.runtime()), \
                patch("speech_controller.runtime.RuntimeServer") as server, patch("builtins.print"):
            main(["--config", str(self.root / "config.json"), "--check"])
            server.assert_not_called()
        result = subprocess.run([sys.executable, "-m", "speech_controller.runtime", "--config",
            str(self.root / "absent.json"), "--check"], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def manifest(self):
        policy = asdict(self.policy)
        policy.pop("daemon_id")
        return dict(version=1, actor="test-service", port=8093, docker_socket=self.config.docker_socket,
            daemon_id="test-daemon", traffic_path=self.config.traffic_path, journal_path=self.config.journal_path,
            policy=policy, deployments=[asdict(approval) | dict(task=target.task, languages=list(target.languages),
                base_url=target.base_url, network_id=binding.network_id, address=binding.address)
                for approval, target, binding in zip(self.approvals, self.targets, self.bindings)])

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX private-file and socket semantics")
    def test_private_manifest_load_and_strict_schema(self):
        manifest = self.root / "config.json"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(self.config.docker_socket)
            def write(value):
                manifest.write_text(value if isinstance(value, str) else json.dumps(value))
                manifest.chmod(0o600)
            write(self.manifest())
            loaded = RuntimeConfiguration.load(str(manifest), TOKEN)
            self.assertEqual(loaded, self.config)
            self.assertNotIn(TOKEN, repr(loaded))
            for value in (self.manifest() | {"extra": 1}, self.manifest() | {"version": True},
                    self.manifest() | {"deployments": []}, self.manifest() | {"token": TOKEN},
                    '{"version":1,"version":1}', " " * 65537):
                write(value)
                with self.assertRaises(ValueError):
                    RuntimeConfiguration.load(str(manifest), TOKEN)
            write(self.manifest())
            manifest.chmod(0o644)
            with self.assertRaises(ValueError):
                RuntimeConfiguration.load(str(manifest), TOKEN)

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX private-file semantics")
    def test_private_file_rejects_symlinks_hardlinks_and_open_parent(self):
        alias = self.root / "alias"
        alias.symlink_to(self.traffic_path)
        with self.assertRaises(ValueError):
            private_file(str(alias))
        alias.unlink()
        os.link(self.traffic_path, alias)
        with self.assertRaises(ValueError):
            private_file(str(self.traffic_path))
        alias.unlink()
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            private_file(str(self.traffic_path))
        self.root.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
