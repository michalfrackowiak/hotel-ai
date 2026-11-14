import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel


MODEL_NAMES = {"en": "vosk-model-small-en-us-0.15", "pl": "vosk-model-small-pl-0.22"}
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 15
CHUNK_BYTES = SAMPLE_RATE // 2
MAX_CONNECTIONS = 8
LOGGER = logging.getLogger(__name__)


def transcribe(audio, model):
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    segments = []
    for offset in range(0, len(audio), CHUNK_BYTES):
        if recognizer.AcceptWaveform(audio[offset : offset + CHUNK_BYTES]):
            segments.append(json.loads(recognizer.Result())["text"])
    segments.append(json.loads(recognizer.FinalResult())["text"])
    return " ".join(segment for segment in segments if segment)


class RecognitionServer(ThreadingHTTPServer):
    def __init__(self):
        SetLogLevel(-1)
        self.models = {language: Model(str(Path("/models") / name)) for language, name in MODEL_NAMES.items()}
        self.inference = threading.BoundedSemaphore(1)
        self.connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(("0.0.0.0", 5001), RecognitionHandler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(5)
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        LOGGER.warning("Speech recognition connection failed")


class RecognitionHandler(BaseHTTPRequestHandler):
    server: RecognitionServer

    def log_message(self, format, *args):
        # Keep request lines and any guest data out of service logs.
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.respond(200, {"status": "ok"})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self):
        route, _, query = self.path.partition("?")
        if route != "/transcribe":
            self.respond(404, {"error": "not found"})
            return

        language = query.removeprefix("language=")
        if query != f"language={language}" or language not in self.server.models:
            self.respond(400, {"error": "language must be en or pl"})
            return

        if self.headers.get_content_type() != "application/octet-stream" or self.headers.get("Transfer-Encoding"):
            self.respond(415, {"error": "expected raw PCM16 audio"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_AUDIO_BYTES:
            self.respond(413, {"error": "audio too large"})
            return
        if length <= 0 or length % 2:
            self.respond(400, {"error": "expected complete PCM16 samples"})
            return

        audio = self.rfile.read(length)
        if len(audio) != length:
            self.respond(400, {"error": "incomplete audio"})
            return
        if not self.server.inference.acquire(blocking=False):
            self.respond(429, {"error": "recognizer busy"})
            return

        try:
            text = transcribe(audio, self.server.models[language])
        except Exception as error:
            LOGGER.warning("Speech recognition failed (%s)", type(error).__name__)
            self.respond(503, {"error": "recognition failed"})
            return
        finally:
            self.server.inference.release()

        self.respond(200, {"text": text})

    def respond(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    with RecognitionServer() as server:
        server.serve_forever()
