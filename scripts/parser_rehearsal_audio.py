"""Prepare WAV input for Chromium's fictional microphone fixture."""

import io
import struct
import wave


MAX_RECORDED_WAV_BYTES = 512 * 1024


def write_fixture_wav(path, source, frames):
    with wave.open(str(path), "wb") as output:
        output.setparams(source.getparams())
        silence = b"\0" * source.getnchannels() * source.getsampwidth()
        # The fake device clock can run while the prompt plays.
        output.writeframes(silence * (7 * source.getframerate()))
        output.writeframes(frames)
        output.writeframes(silence * (2 * source.getframerate()))


def prepare_recorded_wav(source_path, destination):
    if not source_path.is_file():
        raise ValueError("Recorded WAV must be a regular file.")
    with source_path.open("rb") as source_file:
        audio = source_file.read(MAX_RECORDED_WAV_BYTES + 1)
    if len(audio) > MAX_RECORDED_WAV_BYTES:
        raise ValueError("Recorded WAV exceeds 512 KiB.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            rate = source.getframerate()
            frame_count = source.getnframes()
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or not 16000 <= rate <= 48000:
                raise ValueError("Recorded WAV must be 16-bit mono PCM at 16–48 kHz.")
            if not rate / 4 <= frame_count <= 5 * rate:
                raise ValueError("Recorded WAV must be 0.25–5 seconds long.")
            frames = source.readframes(frame_count)
            if len(frames) != 2 * frame_count:
                raise ValueError("Recorded WAV audio is truncated.")
            write_fixture_wav(destination, source, frames)
    except (EOFError, struct.error, wave.Error) as error:
        raise ValueError("Invalid recorded PCM WAV.") from error
