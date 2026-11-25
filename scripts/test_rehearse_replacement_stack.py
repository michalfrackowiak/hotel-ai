import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rehearse_replacement_stack as replacement


class ReplacementStackTests(unittest.TestCase):
    def test_snapshot_checks_and_timing_evidence(self):
        checksum = "ab" * 32
        images = [{"Os": "linux", "Architecture": "amd64", "Id": role} for role in ("web", "piper", "asr")]
        cases = (
            ("unchanged", (checksum, checksum), 1, 3, None),
            ("stdout-only", (checksum, checksum), 1, 3, None),
            ("malformed", ("invalid",), 0, 0, "valid SHA-256"),
            ("restore-failure", (checksum,), 1, 0, "restore verification failed"),
            ("source-changed", (checksum, "cd" * 32), 1, 3, "Source snapshot changed"),
            ("cleanup-failure", (checksum, checksum), 1, 3, "Cleanup failed"),
            ("existing-evidence", (checksum, checksum), 1, 3, None),
        )
        for outcome, digests, restore_count, runtime_count, error in cases:
            with self.subTest(outcome=outcome), TemporaryDirectory() as temporary:
                timings_json = Path(temporary) / "timings.json"
                if outcome == "existing-evidence":
                    timings_json.write_text("previous evidence\n", encoding="utf-8")
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
                        patch.object(replacement, "monotonic", side_effect=(10, 15, 17, 18, 21, 25, 26)), \
                        patch("builtins.print") as printed:
                    if outcome == "cleanup-failure":
                        cleanup.side_effect = RuntimeError("Cleanup failed")
                    if outcome == "existing-evidence":
                        with self.assertRaises(FileExistsError):
                            replacement.rehearse("web", "piper", "asr", timings_json=timings_json)
                    elif outcome == "stdout-only":
                        replacement.rehearse("web", "piper", "asr")
                    elif error is None:
                        replacement.rehearse("web", "piper", "asr", timings_json=timings_json)
                    else:
                        with self.assertRaisesRegex(RuntimeError, error) as failure:
                            replacement.rehearse("web", "piper", "asr", timings_json=timings_json)
                        self.assertNotIn(checksum, str(failure.exception))
                        if outcome == "restore-failure":
                            self.assertTrue(failure.exception.__suppress_context__)

                cleanup.assert_called_once()
                restores = [call.args for call in calls.call_args_list if "restore-fixture" in call.args]
                launches = [call.args for call in calls.call_args_list if call.args[:2] == ("run", "-d")]
                self.assertEqual(len(restores), restore_count)
                self.assertEqual(len(launches), runtime_count)
                self.assertEqual(list(results), [])
                prefix = "Replacement rehearsal timings: "
                timings = [call.args[0].removeprefix(prefix) for call in printed.call_args_list
                           if call.args[0].startswith(prefix)]
                if outcome in ("unchanged", "stdout-only"):
                    self.assertEqual(len(timings), 1)
                    self.assertEqual(json.loads(timings[0]), {
                        "restore_verify_and_promote_seconds": 2,
                        "cached_launch_to_all_ready_seconds": 3,
                        "web_and_speech_checks_seconds": 4,
                        "total_before_cleanup_seconds": 16,
                    })
                    if outcome == "unchanged":
                        self.assertEqual(json.loads(timings_json.read_text(encoding="utf-8")), json.loads(timings[0]))
                    else:
                        self.assertFalse(timings_json.exists())
                else:
                    self.assertEqual(timings, [])
                    self.assertFalse(any("Rehearsal checks passed" in call.args[0] for call in printed.call_args_list))
                    if outcome == "existing-evidence":
                        self.assertEqual(timings_json.read_text(encoding="utf-8"), "previous evidence\n")
                    else:
                        self.assertFalse(timings_json.exists())
                for restore in restores:
                    self.assertIn('--expected-sha256 "$1"', restore[-3])
                    self.assertNotIn(checksum, restore[-3])
                    self.assertEqual(restore[-1], checksum)
