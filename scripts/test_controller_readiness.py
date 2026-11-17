"""Synthetic readiness contracts exercised against a local HTTP fixture."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
from threading import Thread
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_controller.readiness import PHRASES, ProbeTarget, ReadinessProbes, _probe_once
from scripts import smoke_controller_readiness


def wav_bytes(channels=1, width=2, rate=22050, frames=100):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0" * channels * width * frames)
    return output.getvalue()


class FixtureHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.calls.append((self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
        status, content_type, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Location", "/must-not-follow")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # Rejected headers/oversized responses may close the client early.

    def do_GET(self):
        self.server.calls.append((self.path, self.headers, b""))
        self.send_error(500)

    def log_message(self, *_args):
        pass


class ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.reply = (200, "audio/wav", wav_bytes())
        self.probes = ReadinessProbes([
            ProbeTarget("piper", "tts", self.url, ("en", "pl")),
            ProbeTarget("asr", "stt", self.url, ("en", "pl")),
        ])

    def test_tts_both_languages_send_fixed_text_and_validate_wav(self):
        for language in ("en", "pl"):
            self.assertTrue(self.probes("piper", (language, "tts")))
            path, headers, body = self.server.calls[-1]
            self.assertEqual(path, "/synthesize")
            self.assertEqual(headers.get_content_type(), "application/json")
            self.assertEqual(headers["Accept"], "audio/wav")
            self.assertEqual(json.loads(body), {"language": language, "text": PHRASES[language]})

    def test_stt_both_languages_send_one_second_pcm_silence(self):
        self.server.reply = (200, "application/json", b'{"text":""}')
        for language in ("en", "pl"):
            self.assertTrue(self.probes("asr", (language, "stt")))
            path, headers, body = self.server.calls[-1]
            self.assertEqual(path, f"/transcribe?language={language}")
            self.assertEqual(headers.get_content_type(), "application/octet-stream")
            self.assertEqual(headers["Accept"], "application/json")
            self.assertEqual(body, b"\0" * 32000)

    def test_unapproved_names_and_slots_never_open_http(self):
        with patch("speech_controller.readiness.subprocess.Popen") as opener:
            for name, slot in (("missing", ("en", "tts")), (None, ("en", "tts")),
                               ("piper", ("de", "tts")), ("piper", ("en", "stt")),
                               ("piper", ["en", "tts"]), ("piper", ("en",)),
                               ("piper", ([], "tts"))):
                with self.subTest(name=name, slot=slot):
                    self.assertFalse(self.probes(name, slot))
            english_only = ReadinessProbes([ProbeTarget("piper", "tts", self.url, ("en",))])
            self.assertFalse(english_only("piper", ("pl", "tts")))
            opener.assert_not_called()

    def test_wrong_status_busy_and_redirects_fail_without_retry_or_follow(self):
        for status in (201, 204, 301, 302, 307, 308, 400, 429, 503):
            with self.subTest(status=status):
                self.server.reply = (status, "audio/wav", wav_bytes())
                before = len(self.server.calls)
                self.assertFalse(self.probes("piper", ("en", "tts")))
                self.assertEqual(len(self.server.calls), before + 1)
                self.assertEqual(self.server.calls[-1][0], "/synthesize")

    def test_wrong_content_types_fail(self):
        for name, task, content_type, body in (
            ("piper", "tts", "application/octet-stream", wav_bytes()),
            ("asr", "stt", "text/plain", b'{"text":""}'),
        ):
            self.server.reply = (200, content_type, body)
            self.assertFalse(self.probes(name, ("en", task)))

    def test_invalid_wav_format_duration_and_incomplete_frames_fail(self):
        for body in (b"not wav", wav_bytes(channels=2), wav_bytes(width=1),
                     wav_bytes(rate=16000), wav_bytes(frames=0),
                     wav_bytes(frames=220501), wav_bytes()[:-1], wav_bytes()[:30],
                     bytes.fromhex("524946460c000000574156454a554e4b01000000" + "00" * 32)):
            with self.subTest(length=len(body)):
                self.server.reply = (200, "audio/wav", body)
                self.assertFalse(self.probes("piper", ("en", "tts")))

    def test_oversized_responses_fail(self):
        for name, task, content_type, body in (
            ("piper", "tts", "audio/wav", wav_bytes() + b"\0" * 1048576),
            ("asr", "stt", "application/json", b'{"text":""}' + b" " * 4096),
        ):
            self.server.reply = (200, content_type, body)
            self.assertFalse(self.probes(name, ("en", task)))

    def test_stt_requires_exact_empty_transcript_object(self):
        for body in (b"", b"{", b"\xff", b"null", b"[]", b'{"text":false}',
                     b'{"text":"noise"}', b'{"text":"","busy":false}',
                     b'{"text":"noise","text":""}', b'{"text":""} trailing'):
            with self.subTest(body=body):
                self.server.reply = (200, "application/json", body)
                self.assertFalse(self.probes("asr", ("en", "stt")))

    def test_environment_proxy_is_not_used(self):
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1",
                                     "HTTP_PROXY": "http://127.0.0.1:1",
                                     "all_proxy": "http://127.0.0.1:1",
                                     "ALL_PROXY": "http://127.0.0.1:1",
                                     "no_proxy": "", "NO_PROXY": ""}):
            self.assertTrue(self.probes("piper", ("en", "tts")))
        self.assertEqual(len(self.server.calls), 1)

    def test_timeout_fails_after_one_bounded_attempt(self):
        with patch("speech_controller.readiness.build_opener") as build:
            build.return_value.open.side_effect = TimeoutError
            self.assertFalse(_probe_once(ProbeTarget("asr", "stt", self.url, ("en", "pl")), "en"))
            build.return_value.open.assert_called_once()
            self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 8})
        self.assertEqual(self.server.calls, [])


class ConfigurationAndCliTests(unittest.TestCase):
    def test_invalid_target_configuration_is_rejected(self):
        for url in ("file:///tmp/audio", "http://user:secret@piper:5000", "http://piper/path",
                    "http://piper?query=1", "http://piper#fragment", "http://piper:0",
                    "http://piper:65536", "http://piper\n", "http://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ProbeTarget("piper", "tts", url, ("en", "pl"))
        for name, task, languages in (("", "tts", ("en",)), ("piper", "unknown", ("en",)),
                                      ("piper", "tts", ()), ("piper", "tts", ["en"]),
                                      ("piper", "tts", ("de",)), ("piper", "tts", ("en", "en"))):
            with self.subTest(name=name, task=task, languages=languages), self.assertRaises(ValueError):
                ProbeTarget(name, task, "http://piper:5000", languages)

    def test_registry_rejects_empty_invalid_and_duplicate_targets(self):
        target = ProbeTarget("piper", "tts", "http://piper:5000", ("en", "pl"))
        for targets in ([], [object()], [target, target]):
            with self.assertRaises(ValueError):
                ReadinessProbes(targets)

    def test_cli_attempts_all_four_probes_and_returns_failure_if_any_fail(self):
        for results, expected in (([True] * 4, 0), ([False, True, True, True], 1)):
            output = io.StringIO()
            with patch.object(smoke_controller_readiness, "ReadinessProbes") as factory, redirect_stdout(output):
                factory.return_value.side_effect = results
                status = smoke_controller_readiness.main([
                    "--tts-url", "http://piper:5000", "--stt-url", "http://asr:5001",
                ])
            self.assertEqual(status, expected)
            self.assertEqual(factory.call_args.args[0], [
                ProbeTarget("piper", "tts", "http://piper:5000", ("en", "pl")),
                ProbeTarget("asr", "stt", "http://asr:5001", ("en", "pl")),
            ])
            calls = [("piper", ("en", "tts")), ("piper", ("pl", "tts")),
                     ("asr", ("en", "stt")), ("asr", ("pl", "stt"))]
            self.assertEqual([call.args for call in factory.return_value.call_args_list], calls)
            self.assertEqual(output.getvalue().splitlines(), [
                f"{name}/{slot[0]}: success={result}" for (name, slot), result in zip(calls, results)
            ])


if __name__ == "__main__":
    unittest.main()
