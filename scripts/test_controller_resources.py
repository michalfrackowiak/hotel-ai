from collections import namedtuple
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.resources import host_resources
from speech_controller.service import Configuration

Usage = namedtuple('Usage', 'total used free')


class HostResourcesTests(unittest.TestCase):
    def observe(self, text, storage='/fixture'):
        with patch('speech_controller.resources.platform.system', return_value='Linux'), \
             patch('speech_controller.resources.os.cpu_count', return_value=4), \
             patch('speech_controller.resources.Path.open', return_value=StringIO(text)), \
             patch('speech_controller.resources.shutil.disk_usage', return_value=Usage(10485760, 0, 5242880)):
            return host_resources(storage)

    def test_linux_metrics_and_no_paths(self):
        result = self.observe('MemTotal: 8192 kB\nMemAvailable: 2048 kB\n')
        self.assertEqual(result, dict(scope='controller_host', platform='linux', cpu_count=4,
            memory_total_mib=8, memory_available_mib=2, disk_total_mib=10, disk_free_mib=5))
        self.assertNotIn('/fixture', str(result))
        result = self.observe('MemTotal: 8192 kB\nMemAvailable: 0 kB\n')
        self.assertEqual(result['memory_available_mib'], 0)

    def test_invalid_memory_preserves_independent_metrics(self):
        for text in ('', 'MemTotal: 8192 MB\nMemAvailable: 2048 kB\n',
                     'MemTotal: 8192 kB\nMemAvailable: -1 kB\n',
                     'MemTotal: 8192 kB\nMemAvailable: 9000 kB\n',
                     'MemTotal: 0 kB\nMemAvailable: 0 kB\n',
                     'MemTotal: 8192 kB\nMemTotal: 8192 kB\nMemAvailable: 0 kB\n'):
            with self.subTest(text=text):
                result = self.observe(text)
                self.assertIsNone(result['memory_total_mib'])
                self.assertIsNone(result['memory_available_mib'])
                self.assertEqual(result['disk_free_mib'], 5)

    def test_unknown_storage_and_unsupported_platform(self):
        with patch('speech_controller.resources.platform.system', return_value='Linux'), \
             patch('speech_controller.resources.Path.open', side_effect=PermissionError), \
             patch('speech_controller.resources.shutil.disk_usage', side_effect=PermissionError) as disk:
            result = host_resources()
            disk.assert_not_called()
            self.assertIsNone(result['disk_free_mib'])
            self.assertIsNone(host_resources('/private')['disk_total_mib'])
        with patch('speech_controller.resources.platform.system', return_value='Windows'), \
             patch('speech_controller.resources.os.cpu_count') as cpu, \
             patch('speech_controller.resources.shutil.disk_usage') as disk:
            result = host_resources('/private')
            self.assertEqual(result['platform'], 'unsupported')
            self.assertTrue(all(value is None for key, value in result.items() if key not in ('scope', 'platform')))
            cpu.assert_not_called()
            disk.assert_not_called()

    def test_storage_configuration_is_optional_absolute_and_private(self):
        env = dict(HOTEL_SPEECH_CONTROLLER_TOKEN='x' * 32, HOTEL_SPEECH_CONTROLLER_HOST='host',
                   HOTEL_SPEECH_CONTROLLER_CONTAINERS='["fixture"]')
        self.assertEqual(Configuration.from_env(env).storage_path, '')
        absolute = str(Path.cwd())
        self.assertEqual(Configuration.from_env(env | {'HOTEL_SPEECH_CONTROLLER_STORAGE_PATH': absolute}).storage_path, absolute)
        for path in ('relative/private', absolute + '\nprivate'):
            with self.assertRaises(ValueError) as caught:
                Configuration.from_env(env | {'HOTEL_SPEECH_CONTROLLER_STORAGE_PATH': path})
            self.assertNotIn(path, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
