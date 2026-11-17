import io
import json
import wave
from contextlib import nullcontext
from functools import lru_cache
from http.client import HTTPException
from threading import BoundedSemaphore
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from speech_controller.traffic import TrafficBusy, TrafficGate, TrafficUnavailable
from speech_controller.routing import RoutedTrafficGate, load_targets


MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARACTERS = 1000
MAX_PCM_BYTES = 480000
MAX_TRANSCRIPT_BYTES = 8192
# Bound outbound requests to one inference plus one runtime queue slot. Waiting
# happens only at the engine, so the web process adds no second queue.
generation_slot = BoundedSemaphore(2)
transcription_slot = BoundedSemaphore(2)
# The deployed Gunicorn worker has four threads. Leave one available for chat
# and health even when both engines have waiting requests.
speech_slots = BoundedSemaphore(3)


class SpeechUnavailable(RuntimeError):
    pass


class SpeechBusy(SpeechUnavailable):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _read_response(opener, *, data, timeout, limit, slot):
    """Track outbound work across processes when private traffic gating is enabled."""
    try:
        path = settings.HOTEL_SPEECH_TRAFFIC_DB
        routed = bool(settings.HOTEL_SPEECH_ROUTE_TARGETS)
        if routed:
            if not path:
                raise ValueError("Routing requires a shared traffic store.")
            claim = RoutedTrafficGate(path, load_targets(settings.HOTEL_SPEECH_ROUTE_TARGETS)).claim(slot)
        else:
            claim = TrafficGate(path).claim(slot) if path else nullcontext(None)
    except (ValueError, TrafficUnavailable) as error:
        raise SpeechUnavailable("The speech traffic gate is unavailable.") from error
    try:
        with claim as ticket:
            language, task = slot
            base_url = ticket.target.base_url if routed else (
                settings.HOTEL_PIPER_URL if task == "tts" else settings.HOTEL_ASR_URL)
            endpoint = "/synthesize" if task == "tts" else "/transcribe?language=" + language
            request = Request(base_url.rstrip("/") + endpoint, data=data, method="POST", headers={
                "Content-Type": "application/json" if task == "tts" else "application/octet-stream",
                "Accept": "audio/wav" if task == "tts" else "application/json",
            })
            try:
                with opener.open(request, timeout=timeout) as response:
                    body = response.read(limit + 1)
                    remaining = getattr(response, "length", None)
                    if ticket is not None and (len(body) > limit or
                                               isinstance(remaining, int) and remaining > 0):
                        raise SpeechUnavailable("The speech response did not complete.")
                    return body
            except HTTPError as error:
                # Only a trusted busy response proves this attempt was not admitted.
                if ticket is not None and error.code != 429:
                    ticket.mark_uncertain()
                raise
            except BaseException:
                # A disconnected caller does not prove that remote inference ended.
                if ticket is not None:
                    ticket.mark_uncertain()
                raise
    except TrafficBusy as error:
        raise SpeechBusy("The speech service is paused or busy.") from error
    except TrafficUnavailable as error:
        raise SpeechUnavailable("The speech traffic gate is unavailable.") from error


def synthesize(reply, language):
    if language not in {"en", "pl"} or not reply or len(reply) > MAX_TEXT_CHARACTERS:
        raise SpeechUnavailable("The speech engine cannot synthesize this reply.")
    if not generation_slot.acquire(blocking=False):
        raise SpeechBusy("The speech engine is busy.")
    if not speech_slots.acquire(blocking=False):
        generation_slot.release()
        raise SpeechBusy("The speech service is busy.")
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        audio = _read_response(opener,
                               data=json.dumps({"text": reply, "language": language}, ensure_ascii=False).encode("utf-8"),
                               timeout=settings.HOTEL_PIPER_TIMEOUT,
                               limit=MAX_AUDIO_BYTES, slot=(language, "tts"))
    except HTTPError as error:
        if error.code == 429:
            raise SpeechBusy("The speech engine is busy.") from error
        raise SpeechUnavailable("The speech engine did not return audio.") from error
    except (OSError, HTTPException, ValueError) as error:
        raise SpeechUnavailable("The speech engine did not return audio.") from error
    finally:
        speech_slots.release()
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


def synthesize_welcome(parts):
    # Route changes must never replay audio cached from an earlier provider.
    return _welcome_audio(parts) if settings.HOTEL_SPEECH_ROUTE_TARGETS else _cached_welcome(parts)


@lru_cache(maxsize=1)
def _cached_welcome(parts):
    return _welcome_audio(parts)


def _welcome_audio(parts):
    pcm = bytearray()
    sample_rate = None
    for language, text in parts:
        audio = synthesize(text, language)
        with wave.open(io.BytesIO(audio), "rb") as segment:
            if sample_rate is None:
                sample_rate = segment.getframerate()
            elif segment.getframerate() != sample_rate:
                raise SpeechUnavailable("Welcome voices use different sample rates.")
            frames = segment.readframes(segment.getnframes())
            if 44 + len(pcm) + len(frames) > MAX_AUDIO_BYTES:
                raise SpeechUnavailable("The welcome exceeded the audio size limit.")
            pcm.extend(frames)
    output = io.BytesIO()
    with wave.open(output, "wb") as merged:
        merged.setnchannels(1)
        merged.setsampwidth(2)
        merged.setframerate(sample_rate)
        merged.writeframes(pcm)
    return output.getvalue()


def transcribe(pcm, language):
    if language not in {"en", "pl"}:
        raise SpeechUnavailable("The speech recognizer is unavailable.")
    if not transcription_slot.acquire(blocking=False):
        raise SpeechBusy("The speech recognizer is busy.")
    if not speech_slots.acquire(blocking=False):
        transcription_slot.release()
        raise SpeechBusy("The speech service is busy.")
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        body = _read_response(opener, data=pcm, timeout=12,
                              limit=MAX_TRANSCRIPT_BYTES, slot=(language, "stt"))
        if len(body) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("transcript response too large")
        result = json.loads(body)
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise ValueError("invalid transcript response")
    except HTTPError as error:
        if error.code == 429:
            raise SpeechBusy("The speech recognizer is busy.") from error
        raise SpeechUnavailable("The speech recognizer did not return a transcript.") from error
    except (OSError, HTTPException, ValueError, RecursionError) as error:
        raise SpeechUnavailable("The speech recognizer did not return a transcript.") from error
    finally:
        speech_slots.release()
        transcription_slot.release()
    return text.strip()


def transcribe_welcome(pcm, language):
    text = transcribe(pcm, language)
    if language != "pl":
        return text
    try:
        english = transcribe(pcm, "en").casefold().strip().rstrip(".!?")
        if english in {"english", "english please"}:
            return "English"
    except SpeechUnavailable:
        # A failed second pass still leaves the Polish transcript usable.
        pass
    return text
