from dataclasses import replace
from types import SimpleNamespace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.service import Configuration, InventoryUnavailable, inventory
from test_controller_inventory import ENV, docker_output, info_output


class DaemonIdentityTests(unittest.TestCase):
    def test_pinned_socket_configuration_requires_linux_real_socket_and_pair(self):
        env = ENV | {"HOTEL_SPEECH_CONTROLLER_DOCKER_SOCKET": "/private/docker.sock",
                     "HOTEL_SPEECH_CONTROLLER_EXPECTED_DAEMON_ID": "fixture-daemon"}
        with patch("speech_controller.service.platform.system", return_value="Linux"), \
             patch("speech_controller.service.os.path.isabs", return_value=True), \
             patch("speech_controller.service.os.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)):
            config = Configuration.from_env(env)
            self.assertEqual(config.expected_daemon_id, "fixture-daemon")
            self.assertNotIn("/private", repr(config))
            for change in ({"HOTEL_SPEECH_CONTROLLER_DOCKER_SOCKET": ""},
                           {"HOTEL_SPEECH_CONTROLLER_EXPECTED_DAEMON_ID": ""},
                           {"HOTEL_SPEECH_CONTROLLER_EXPECTED_DAEMON_ID": "bad id"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    Configuration.from_env(env | change)
            for mode in (stat.S_IFREG, stat.S_IFLNK):
                with patch("speech_controller.service.os.lstat", return_value=SimpleNamespace(st_mode=mode)), \
                        self.assertRaises(ValueError):
                    Configuration.from_env(env)
            with patch("speech_controller.service.platform.system", return_value="Windows"), self.assertRaises(ValueError):
                Configuration.from_env(env)

    def test_fixed_endpoint_pin_and_shared_deadline(self):
        config = replace(Configuration.from_env(ENV), docker_socket="/private/docker.sock", expected_daemon_id="fixture-daemon")
        hostile = {key: "secret-override" for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_TLS")}
        with patch.dict(os.environ, hostile), \
             patch("speech_controller.service.platform.system", return_value="Linux"), \
             patch("speech_controller.service.os.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
             patch("speech_controller.service.monotonic", side_effect=[100, 101, 103]), \
             patch("speech_controller.service.subprocess.run", side_effect=[
                 subprocess.CompletedProcess([], 0, info_output()), subprocess.CompletedProcess([], 0, docker_output())]) as run:
            result = inventory(config)
        self.assertEqual(result["daemon_identity"]["connection"], "pinned_local_socket")
        self.assertNotIn("/private", json.dumps(result))
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:3], ["docker", "--host", "unix:///private/docker.sock"])
            self.assertTrue(set(hostile).isdisjoint(call.kwargs["env"]))
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [4, 2])

    def test_wrong_or_malformed_identity_blocks_inspection(self):
        config = Configuration.from_env(ENV)
        invalid = [b"secret-invalid", b"[]", b"x" * 4097]
        base = json.loads(info_output())
        for change in ({"id": ""}, {"id": "bad id"}, {"name": ""}, {"name": "bad\nname"},
                       {"os_type": "other"}, {"secret": "extra"}):
            invalid.append(json.dumps(base | change).encode())
        for output in invalid:
            with self.subTest(output=output[:30]), patch("speech_controller.service.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, output)) as run:
                with self.assertRaisesRegex(InventoryUnavailable, "^Inventory unavailable.$"):
                    inventory(config)
                self.assertEqual(run.call_count, 1)
        config = replace(config, docker_socket="/private/docker.sock", expected_daemon_id="other-daemon")
        with patch("speech_controller.service.platform.system", return_value="Linux"), \
             patch("speech_controller.service.os.lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
             patch("speech_controller.service.subprocess.run", return_value=subprocess.CompletedProcess([], 0, info_output())) as run:
            with self.assertRaises(InventoryUnavailable):
                inventory(config)
            self.assertEqual(run.call_count, 1)

    def test_expired_budget_prevents_second_docker_call(self):
        with patch("speech_controller.service.monotonic", side_effect=[100, 101, 106]), \
             patch("speech_controller.service.subprocess.run", return_value=subprocess.CompletedProcess([], 0, info_output())) as run:
            with self.assertRaises(InventoryUnavailable):
                inventory(Configuration.from_env(ENV))
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
