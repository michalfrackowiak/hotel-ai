"""Bounded synthetic Piper/Vosk probes, not accuracy or endpoint identity proof."""
from dataclasses import dataclass
from http.client import HTTPException
import io
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import wave


PHRASES = {"en": "Welcome to the hotel.", "pl": "Witamy w hotelu."}


@dataclass(frozen=True)
class ProbeTarget:
    name: str
    task: str
    base_url: str
    languages: tuple

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            valid = (isinstance(self.name, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", self.name)
                     and self.task in ("stt", "tts") and type(self.languages) is tuple and self.languages
                     and all(language in PHRASES for language in self.languages)
                     and len(set(self.languages)) == len(self.languages)
                     and url.scheme in ("http", "https") and url.hostname
                     and url.username is None and url.password is None
                     and not url.query and not url.fragment and url.path in ("", "/")
                     and (url.port is None or 1 <= url.port <= 65535)
                     and not any(ord(char) <= 32 for char in self.base_url))
            if not valid:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid locally approved speech probe target.") from None


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def valid_wav(data):
    with wave.open(io.BytesIO(data), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 22050, "NONE"):
            return False
        frames = audio.getnframes()
        return 0 < frames <= 220500 and len(audio.readframes(frames + 1)) == frames * 2


class ReadinessProbes:
    """Local trusted URLs only; one attempt, eight-second socket inactivity timeout.

    No redirects or environment proxies. The caller must bind endpoints to the
    approved containers; a successful response does not prove endpoint identity.
    """
    def __init__(self, targets):
        targets = tuple(targets)
        if not targets or any(not isinstance(target, ProbeTarget) for target in targets):
            raise ValueError("Approved immutable probe targets are required.")
        mapping = {target.name: target for target in targets}
        if len(mapping) != len(targets):
            raise ValueError("Probe target names must be unique.")
        self._targets = MappingProxyType(mapping)

    def __call__(self, name, slot):
        if (not isinstance(name, str) or type(slot) is not tuple or len(slot) != 2
                or any(type(value) is not str for value in slot)):
            return False
        target = self._targets.get(name)
        language, task = slot
        if target is None or target.task != task or language not in target.languages:
            return False
        if task == "tts":
            path, content_type, limit = "/synthesize", "application/json", 1048576
            body = json.dumps({"language": language, "text": PHRASES[language]}).encode()
            expected_type = "audio/wav"
        else:
            path, content_type, limit = "/transcribe?language=" + language, "application/octet-stream", 4096
            body, expected_type = b"\0" * 32000, "application/json"
        request = Request(target.base_url.rstrip("/") + path, data=body,
                          headers={"Content-Type": content_type, "Accept": expected_type})
        try:
            with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=8) as response:
                if response.status != 200 or response.headers.get_content_type() != expected_type:
                    return False
                data = response.read(limit + 1)
            if len(data) > limit:
                return False
            return valid_wav(data) if task == "tts" else json.loads(data, object_pairs_hook=unique_keys) == {"text": ""}
        except (OSError, HTTPException, ValueError, TypeError, RuntimeError, EOFError, wave.Error):
            return False
