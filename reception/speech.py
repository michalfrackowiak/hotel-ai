import io
import json
import wave
from http.client import HTTPException
from threading import BoundedSemaphore
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings


MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARACTERS = 1000
MAX_PCM_BYTES = 480000
MAX_TRANSCRIPT_BYTES = 8192
generation_slot = BoundedSemaphore(1)
transcription_slot = BoundedSemaphore(1)


class SpeechUnavailable(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def synthesize(reply, language):
    if language not in {"en", "pl"} or not reply or len(reply) > MAX_TEXT_CHARACTERS:
        raise SpeechUnavailable("The speech engine cannot synthesize this reply.")
    if not generation_slot.acquire(blocking=False):
        raise SpeechUnavailable("The speech engine is busy.")
    try:
        request = Request(
            f"{settings.HOTEL_PIPER_URL}/synthesize",
            data=json.dumps({"text": reply, "language": language}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=settings.HOTEL_PIPER_TIMEOUT) as response:
            audio = response.read(MAX_AUDIO_BYTES + 1)
    except (OSError, HTTPException, ValueError) as error:
        raise SpeechUnavailable("The speech engine did not return audio.") from error
    finally:
        generation_slot.release()
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechUnavailable("The speech engine returned invalid audio.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth()) != (1, 2):
                raise wave.Error("expected 16-bit mono PCM")
            if not 16000 <= wav.getframerate() <= 24000:
                raise wave.Error("unexpected sample rate")
            frames = wav.getnframes()
            if frames <= 0 or len(wav.readframes(frames)) != 2 * frames:
                raise wave.Error("empty or incomplete WAV")
    except (wave.Error, EOFError) as error:
        raise SpeechUnavailable("The speech engine returned invalid audio.") from error
    return audio


def transcribe(pcm, language):
    if language not in {"en", "pl"} or not transcription_slot.acquire(blocking=False):
        raise SpeechUnavailable("The speech recognizer is unavailable.")
    try:
        request = Request(
            f"{settings.HOTEL_ASR_URL}/transcribe?language={language}",
            data=pcm,
            method="POST",
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=12) as response:
            body = response.read(MAX_TRANSCRIPT_BYTES + 1)
        if len(body) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("transcript response too large")
        result = json.loads(body)
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise ValueError("invalid transcript response")
    except (OSError, HTTPException, ValueError, RecursionError) as error:
        raise SpeechUnavailable("The speech recognizer did not return a transcript.") from error
    finally:
        transcription_slot.release()
    return text.strip()
