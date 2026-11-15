"""Run a bounded, sequential synthetic benchmark against private speech services.

Inside the configured web container, feed this file to python on stdin and pass
--django-semantic to check the installed booking parsers as well as transcripts.
For local adapters, set --piper-url and --asr-url to their loopback origins.
"""

import argparse
from array import array
from datetime import datetime, timedelta, timezone
import io
import json
import math
import os
from pathlib import Path
import platform
import sys
from time import monotonic
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import wave


SAMPLE_RATE = 16000
MAX_PCM_BYTES = SAMPLE_RATE * 2 * 15
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 8192
CORPUS = (
    ("en", "arrival", "tomorrow"),
    ("en", "duration", "for three nights"),
    ("en", "guests", "two guests"),
    ("en", "email", "guest at example dot com"),
    ("en", "phone", "plus four eight five zero one two three four five six seven"),
    ("pl", "arrival", "jutro"),
    ("pl", "duration", "na trzy noce"),
    ("pl", "guests", "dwie osoby"),
    ("pl", "email", "gosc małpa example kropka com"),
    ("pl", "phone", "plus cztery osiem pięć zero jeden dwa trzy cztery pięć sześć siedem"),
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def origin(value):
    url = urlsplit(value)
    if (
        url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
        or url.path not in ("", "/") or url.query or url.fragment
    ):
        raise argparse.ArgumentTypeError("Use an HTTP origin without credentials, a path, or query parameters.")
    try:
        url.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a valid origin port.") from error
    return value.rstrip("/")


def bounded_integer(minimum, maximum):
    def parse(value):
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Choose a value between {minimum} and {maximum}.")
        return number
    return parse


def post(client, url, payload, content_type, expected_type, limit, timeout):
    request = Request(url, data=payload, method="POST", headers={"Content-Type": content_type})
    started = monotonic()
    with client.open(request, timeout=timeout) as response:
        if response.headers.get_content_type() != expected_type:
            raise ValueError("Unexpected service content type.")
        body = response.read(limit + 1)
    elapsed = monotonic() - started
    if len(body) > limit:
        raise ValueError("Speech response exceeds the benchmark limit.")
    return body, elapsed


def pcm_from_wav(audio):
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
            raise ValueError("Expected mono PCM16 WAV.")
        rate = wav.getframerate()
        if not 16000 <= rate <= 24000:
            raise ValueError("Unsupported WAV sample rate.")
        count = wav.getnframes()
        if not 0 < count <= rate * 15:
            raise ValueError("Synthetic audio must be between zero and fifteen seconds.")
        raw = wav.readframes(count)
    if len(raw) != count * 2:
        raise ValueError("Incomplete WAV data.")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if rate != SAMPLE_RATE:
        length = round(len(samples) * SAMPLE_RATE / rate)
        converted = array("h")
        for index in range(length):
            position = index * rate / SAMPLE_RATE
            first = int(position)
            second = min(first + 1, len(samples) - 1)
            value = samples[first] + (samples[second] - samples[first]) * (position - first)
            converted.append(round(value))
        samples = converted
    if sys.byteorder != "little":
        samples.byteswap()
    pcm = samples.tobytes()
    if len(pcm) > MAX_PCM_BYTES:
        raise ValueError("Resampled audio exceeds Vosk's input limit.")
    return pcm, len(pcm) / (SAMPLE_RATE * 2)


def recognize(client, asr_url, pcm, language, timeout):
    body, elapsed = post(
        client, f"{asr_url}/transcribe?language={language}", pcm,
        "application/octet-stream", "application/json", MAX_JSON_BYTES, timeout,
    )
    result = json.loads(body)
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise ValueError("Invalid transcript JSON.")
    return result["text"].strip(), elapsed


def normalized(text):
    return " ".join(text.casefold().split()).rstrip(".!?")


def semantic_evaluator():
    repository = Path(__file__).resolve().parent.parent
    if (repository / "config" / "settings.py").is_file():
        sys.path.insert(0, str(repository))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.utils import timezone as django_timezone
    from reception.contacts import parse_email, parse_phone
    from reception.parsing import parse_stay_input

    reference = django_timezone.localdate()
    arrival = reference + timedelta(days=7)

    def evaluate(language, category, text):
        if category == "silence":
            return text == ""
        if category == "email":
            expected = "guest@example.com" if language == "en" else "gosc@example.com"
            return parse_email(text) == expected
        if category == "phone":
            return parse_phone(text) == "+48501234567"
        stage = {"arrival": "check_in", "duration": "check_out", "guests": "guests"}[category]
        actual = parse_stay_input(stage, text, language, check_in=arrival if category == "duration" else None)
        expected = {"arrival": reference + timedelta(days=1), "duration": arrival + timedelta(days=3), "guests": 2}[category]
        return actual == expected

    return evaluate, reference.isoformat()


def percentile(values, percentile_value):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile_value * len(ordered)) - 1)] if ordered else None


def summary(rows):
    successful = [row for row in rows if "error" not in row]
    values = {}
    for field in ("tts_seconds", "asr_seconds", "pipeline_seconds", "tts_rtf", "asr_rtf"):
        series = [row[field] for row in successful if field in row]
        values[field] = {"p50": percentile(series, 0.5), "p95": percentile(series, 0.95)}
    semantics = [row["semantic_correct"] for row in successful if "semantic_correct" in row]
    return {
        "attempts": len(rows), "completed": len(successful), "errors": len(rows) - len(successful),
        "exact_correct": sum(row["exact_match"] for row in successful),
        "exact_denominator": len(successful),
        "semantic_correct": sum(semantics) if semantics else None,
        "semantic_denominator": len(semantics) if semantics else None,
        "timings": values,
    }


def benchmark(args):
    client = build_opener(ProxyHandler({}), NoRedirect())
    evaluate, reference = semantic_evaluator() if args.django_semantic else (None, None)
    rows = []
    started = monotonic()
    for repetition in range(args.repeats):
        for language, category, text in (*CORPUS, ("en", "silence", ""), ("pl", "silence", "")):
            row = {"language": language, "category": category, "repetition": repetition + 1, "input": text}
            try:
                turn_started = monotonic()
                if category == "silence":
                    pcm, duration = bytes(SAMPLE_RATE * 2), 1.0
                else:
                    audio, tts_elapsed = post(
                        client, f"{args.piper_url}/synthesize",
                        json.dumps({"text": text, "language": language}, ensure_ascii=False).encode("utf-8"),
                        "application/json", "audio/wav", MAX_WAV_BYTES, args.timeout,
                    )
                    pcm, duration = pcm_from_wav(audio)
                    row.update(tts_seconds=tts_elapsed, tts_rtf=tts_elapsed / duration)
                transcript, asr_elapsed = recognize(client, args.asr_url, pcm, language, args.timeout)
                row.update(
                    audio_seconds=duration, transcript=transcript,
                    asr_seconds=asr_elapsed, asr_rtf=asr_elapsed / duration,
                    pipeline_seconds=monotonic() - turn_started,
                    exact_match=normalized(transcript) == normalized(text),
                )
                if evaluate:
                    row["semantic_correct"] = evaluate(language, category, transcript)
            except (OSError, HTTPException, ValueError, EOFError, wave.Error) as error:
                row["error"] = type(error).__name__
                if isinstance(error, HTTPError):
                    row["http_status"] = error.code
            rows.append(row)
    return {
        "schema_version": 1, "corpus_version": 1, "label": args.label,
        "captured_at": datetime.now(timezone.utc).isoformat(), "wall_seconds": monotonic() - started,
        "host": {"machine": platform.machine(), "system": platform.system(), "python": platform.python_version()},
        "conditions": {
            "sequential": True, "repeats": args.repeats, "request_timeout_seconds": args.timeout,
            "semantic_reference_date": reference, "django_semantic": args.django_semantic,
            "resampling": "mono PCM16 linear interpolation to 16000 Hz",
            "limitations": [
                "Synthetic Piper speech from fixed fictional phrases. Accuracy does not represent guest microphones, accents, noise, or networks.",
                "The first row per language can include warmup. Repetitions run on the same live model process.",
                "TTS latency includes buffering the complete WAV response. Time to first audio and cold process startup are unmeasured.",
                "No recording, browser playback, speech endpointing, or user-perceived turn latency is measured.",
                "Provider model identity and container resource limits must be recorded separately from deployment inventory.",
                "Semantic checks use the installed deterministic parsers and current parser configuration. No booking or conversation writes occur.",
            ],
        },
        "summary": {language: summary([row for row in rows if row["language"] == language]) for language in ("en", "pl")},
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--piper-url", type=origin, default=os.environ.get("HOTEL_PIPER_URL", "http://piper:5000"))
    parser.add_argument("--asr-url", type=origin, default=os.environ.get("HOTEL_ASR_URL", "http://asr:5001"))
    parser.add_argument("--repeats", type=bounded_integer(1, 5), default=2)
    parser.add_argument("--timeout", type=bounded_integer(1, 30), default=12)
    parser.add_argument("--django-semantic", action="store_true", help="Use installed Django booking parsers for semantic checks.")
    parser.add_argument("--label", default="", help="Optional deployment/commit label recorded verbatim.")
    args = parser.parse_args()
    report = benchmark(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any("error" in row for row in report["results"]))


if __name__ == "__main__":
    sys.exit(main())
