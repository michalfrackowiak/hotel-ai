"""Exercise real Piper synthesis through the private Django listener."""

import io
import json
import sys
import wave
from http.cookiejar import CookieJar
from time import monotonic
from urllib.request import HTTPCookieProcessor, Request, build_opener


def check_speech(base_url, language):
    cookies = CookieJar()
    client = build_opener(HTTPCookieProcessor(cookies))
    with client.open(f"{base_url}/?lang={language}", timeout=15) as response:
        page = response.read().decode()
    if 'data-audio-url="/reception/audio/"' not in page:
        raise ValueError("Reception did not enable Piper")
    csrf = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
    headers = {"Content-Type": "application/json", "X-CSRFToken": csrf}
    start = Request(
        f"{base_url}/reception/message/",
        data=json.dumps({"language": language, "start": True}).encode(),
        headers=headers,
    )
    with client.open(start, timeout=15) as response:
        revision = json.load(response)["revision"]
    request = Request(
        f"{base_url}/reception/audio/",
        data=json.dumps({"revision": revision}).encode(),
        headers=headers,
    )
    started = monotonic()
    with client.open(request, timeout=15) as response:
        if response.headers.get_content_type() != "audio/wav":
            raise ValueError("Reception did not return WAV audio")
        with wave.open(io.BytesIO(response.read()), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 22050):
                raise ValueError("Unexpected WAV format")
            duration = audio.getnframes() / audio.getframerate()
            if duration <= 0:
                raise ValueError("Piper returned empty audio")
    print(f"{language}: {duration:.2f}s audio in {monotonic() - started:.2f}s")


if __name__ == "__main__":
    for locale in ("en", "pl"):
        check_speech(sys.argv[1].rstrip("/"), locale)
