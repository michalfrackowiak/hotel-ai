import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from parser_rehearsal_audio import MAX_RECORDED_WAV_BYTES, prepare_recorded_wav


def wav_bytes(channels=1, width=2, rate=16000, frames=4000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\1" * channels * width * frames)
    return buffer.getvalue()


class RecordedWavTests(unittest.TestCase):
    def test_preparation_preserves_pcm_between_silent_pads(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "spoken.wav"
            destination = Path(temporary) / "fixture.wav"
            original = wav_bytes()
            source.write_bytes(original)
            prepare_recorded_wav(source, destination)
            self.assertEqual(source.read_bytes(), original)
            with wave.open(str(destination), "rb") as prepared:
                self.assertEqual(prepared.getnchannels(), 1)
                self.assertEqual(prepared.getsampwidth(), 2)
                self.assertEqual(prepared.getframerate(), 16000)
                self.assertEqual(prepared.readframes(7 * 16000), b"\0" * 14 * 16000)
                self.assertEqual(prepared.readframes(4000), b"\1" * 8000)
                self.assertEqual(prepared.readframes(2 * 16000), b"\0" * 4 * 16000)
                self.assertEqual(prepared.readframes(1), b"")

    def test_invalid_audio_is_rejected_before_writing(self):
        cases = {
            "oversized": (b"\0" * (MAX_RECORDED_WAV_BYTES + 1), "exceeds"),
            "malformed": (b"not a WAV", "Invalid recorded"),
            "stereo": (wav_bytes(channels=2), "mono PCM"),
            "8-bit": (wav_bytes(width=1), "mono PCM"),
            "rate": (wav_bytes(rate=8000), "mono PCM"),
            "short": (wav_bytes(frames=3999), "seconds long"),
            "long": (wav_bytes(frames=80001), "seconds long"),
            "truncated": (wav_bytes()[:-2], "truncated"),
        }
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "spoken.wav"
            destination = Path(temporary) / "fixture.wav"
            for name, (content, error) in cases.items():
                with self.subTest(name=name):
                    source.write_bytes(content)
                    with self.assertRaisesRegex(ValueError, error):
                        prepare_recorded_wav(source, destination)
                    self.assertFalse(destination.exists())
