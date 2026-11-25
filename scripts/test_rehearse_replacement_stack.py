import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rehearse_replacement_stack as replacement


class ReplacementChecksumTests(unittest.TestCase):
    def test_replacement_checks_snapshot_before_restore_and_after_runtime(self):
        checksum = "ab" * 32
        images = [{"Os": "linux", "Architecture": "amd64", "Id": role} for role in ("web", "piper", "asr")]
        cases = (
            ("unchanged", (checksum, checksum), 1, 3, None),
            ("malformed", ("invalid",), 0, 0, "valid SHA-256"),
            ("restore-failure", (checksum,), 1, 0, "restore verification failed"),
            ("source-changed", (checksum, "cd" * 32), 1, 3, "Source snapshot changed"),
        )
        for outcome, digests, restore_count, runtime_count, error in cases:
            with self.subTest(outcome=outcome):
                results = iter(digests)

                def docker(*args, **kwargs):
                    if args[:2] == ("image", "inspect"):
                        return SimpleNamespace(stdout=json.dumps(images))
                    if args[-1] == replacement.DIGEST:
                        return SimpleNamespace(stdout=next(results) + "\n")
                    if "restore-fixture" in args and outcome == "restore-failure":
                        raise subprocess.CalledProcessError(1, args)

                with patch.object(replacement, "docker", side_effect=docker) as calls, \
                        patch.object(replacement, "require_windows_memory"), \
                        patch.object(replacement, "wait_http"), \
                        patch.object(replacement, "remove_resources") as cleanup, \
                        patch("builtins.print"):
                    if error is None:
                        replacement.rehearse("web", "piper", "asr")
                    else:
                        with self.assertRaisesRegex(RuntimeError, error) as failure:
                            replacement.rehearse("web", "piper", "asr")
                        self.assertNotIn(checksum, str(failure.exception))
                        if outcome == "restore-failure":
                            self.assertTrue(failure.exception.__suppress_context__)

                cleanup.assert_called_once()
                restores = [call.args for call in calls.call_args_list if "restore-fixture" in call.args]
                launches = [call.args for call in calls.call_args_list if call.args[:2] == ("run", "-d")]
                self.assertEqual(len(restores), restore_count)
                self.assertEqual(len(launches), runtime_count)
                self.assertEqual(list(results), [])
                for restore in restores:
                    self.assertIn('--expected-sha256 "$1"', restore[-3])
                    self.assertNotIn(checksum, restore[-3])
                    self.assertEqual(restore[-1], checksum)
