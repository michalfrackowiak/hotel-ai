import io
import json
import wave
from http.client import HTTPException
from threading import BoundedSemaphore
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings


MAX_AUDIO_BYTES = 4 * 1024 * 1024
LANGUAGE_CODES = {"en": "en_us", "pl": "pl"}
generation_slot = BoundedSemaphore(1)


class SpeechUnavailable(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def synthesize(reply, language):
    if not generation_slot.acquire(blocking=False):
        raise SpeechUnavailable("The speech engine is busy.")
    try:
        request = Request(
            f"{settings.HOTEL_ZONOS_URL}/v2/generate",
            data=json.dumps({
                "text": reply,
                "language": LANGUAGE_CODES[language],
                "speaker_embedding_id": settings.HOTEL_ZONOS_VOICE_ID,
                "speaker_mode": "embedding",
                "stream": False,
                "format": "wav",
                "seed": 7,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=settings.HOTEL_ZONOS_TIMEOUT) as response:
            audio = response.read(MAX_AUDIO_BYTES + 1)
    except (OSError, HTTPException, ValueError) as error:
        raise SpeechUnavailable("The speech engine did not return audio.") from error
    finally:
        generation_slot.release()
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechUnavailable("The speech engine returned invalid audio.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 44100):
                raise wave.Error("unexpected WAV format")
            frames = wav.getnframes()
            if frames <= 0 or len(wav.readframes(frames)) != 2 * frames:
                raise wave.Error("empty or incomplete WAV")
    except (wave.Error, EOFError) as error:
        raise SpeechUnavailable("The speech engine returned invalid audio.") from error
    return audio
