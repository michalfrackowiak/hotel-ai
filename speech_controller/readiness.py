"""Bounded synthetic Piper/Vosk probes, not accuracy or endpoint identity proof."""
from dataclasses import asdict, dataclass
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic
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
    """Local trusted URLs only; one attempt in a worker with a total deadline.

    No redirects or environment proxies. The caller must bind endpoints to the
    approved containers; a successful response does not prove endpoint identity.
    """
    def __init__(self, targets, *, timeout=8):
        if type(timeout) not in (int, float) or not 0 < timeout <= 8:
            raise ValueError("Readiness timeout must be positive and at most eight seconds.")
        targets = tuple(targets)
        if not targets or any(not isinstance(target, ProbeTarget) for target in targets):
            raise ValueError("Approved immutable probe targets are required.")
        mapping = {target.name: target for target in targets}
        if len(mapping) != len(targets):
            raise ValueError("Probe target names must be unique.")
        self._targets = MappingProxyType(mapping)
        self._timeout = timeout

    def __call__(self, name, slot):
        deadline = monotonic() + self._timeout
        if (not isinstance(name, str) or type(slot) is not tuple or len(slot) != 2
                or any(type(value) is not str for value in slot)):
            return False
        target = self._targets.get(name)
        language, task = slot
        if target is None or target.task != task or language not in target.languages:
            return False
        payload = json.dumps({"target": asdict(target), "language": language}).encode()
        try:
            with subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ) as process:
                try:
                    process.communicate(payload, timeout=max(0, deadline - monotonic()))
                    return process.returncode == 0 and monotonic() <= deadline
                finally:
                    # A worker never starts descendants. Kill and reap before releasing
                    # the lifecycle lock; OS creation/reaping can add scheduling delay.
                    if process.poll() is None:
                        process.kill()
                    process.wait()
        except (OSError, subprocess.SubprocessError):
            return False


def _probe_once(target, language):
    """Worker-only HTTP operation; the parent enforces its elapsed deadline."""
    if target.task == "tts":
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
        return valid_wav(data) if target.task == "tts" else json.loads(data, object_pairs_hook=unique_keys) == {"text": ""}
    except (OSError, HTTPException, ValueError, TypeError, RuntimeError, EOFError, wave.Error):
        return False


def _worker():
    """Private bounded stdin protocol. Only the exit status leaves the worker."""
    try:
        data = sys.stdin.buffer.read(16385)
        if len(data) > 16384:
            return 1
        config = json.loads(data, object_pairs_hook=unique_keys)
        if set(config) != {"target", "language"}:
            return 1
        values = config["target"]
        values["languages"] = tuple(values["languages"])
        target = ProbeTarget(**values)
        language = config["language"]
        if type(language) is not str or language not in target.languages:
            return 1
        return 0 if _probe_once(target, language) else 1
    except (OSError, ValueError, TypeError, KeyError):
        return 1


if __name__ == "__main__":
    sys.exit(_worker())
