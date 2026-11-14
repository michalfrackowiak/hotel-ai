import io
import json
import logging
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import onnxruntime
from piper import PiperConfig, PiperVoice


VOICE_NAMES = {"en": "en_US-joe-medium", "pl": "pl_PL-darkman-medium"}
MODEL_DIR = Path("/models")
MAX_BODY_BYTES = 8192
MAX_TEXT_CHARS = 1000
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_CONNECTIONS = 8
SOCKET_TIMEOUT = 5
LOGGER = logging.getLogger(__name__)


def load_voice(name):
    model_path = MODEL_DIR / f"{name}.onnx"
    config_path = model_path.with_suffix(".onnx.json")
    config = PiperConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
    session_options = onnxruntime.SessionOptions()
    session_options.intra_op_num_threads = 1
    session_options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=session_options, providers=["CPUExecutionProvider"]
    )
    return PiperVoice(session=session, config=config)


class PiperServer(ThreadingHTTPServer):
    def __init__(self):
        self.voices = {language: load_voice(name) for language, name in VOICE_NAMES.items()}
        self.inference = threading.BoundedSemaphore(1)
        self.connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(("0.0.0.0", 5000), PiperHandler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(SOCKET_TIMEOUT)
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
        LOGGER.warning("Piper HTTP connection failed")


class PiperHandler(BaseHTTPRequestHandler):
    server: PiperServer

    def log_message(self, format, *args):
        # Requests may contain guest information. Keep request lines out of the logs.
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.respond(200, b"ok\n", "text/plain")
            return
        self.reject(404, "not found")

    def do_POST(self):
        if self.path != "/synthesize":
            self.reject(404, "not found")
            return

        if self.headers.get_content_type() != "application/json":
            self.reject(415, "application/json required")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reject(400, "invalid content length")
            return
        if length > MAX_BODY_BYTES:
            self.reject(413, "request too large")
            return
        if length <= 0:
            self.reject(400, "request body required")
            return

        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            self.reject(400, "invalid JSON")
            return
        if not isinstance(request, dict):
            self.reject(400, "JSON object required")
            return

        language = request.get("language")
        text = request.get("text")
        if not isinstance(language, str) or language not in self.server.voices:
            self.reject(400, "language must be en or pl")
            return
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            self.reject(400, f"text must contain 1 to {MAX_TEXT_CHARS} characters")
            return
        try:
            text.encode("utf-8")
        except UnicodeError:
            self.reject(400, "text must be valid Unicode")
            return

        if not self.server.inference.acquire(blocking=False):
            self.reject(429, "synthesizer busy")
            return
        try:
            wav = self.synthesize(text.strip(), self.server.voices[language])
        except Exception as exc:
            LOGGER.warning("Piper synthesis failed (%s)", type(exc).__name__)
            self.reject(503, "synthesis failed")
            return
        finally:
            self.server.inference.release()

        if wav is None:
            self.reject(422, "text produced no audio or exceeded the audio size limit")
            return
        self.respond(200, wav, "audio/wav")

    @staticmethod
    def synthesize(text, voice):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(voice.config.sample_rate)
            for audio in voice.synthesize(text):
                pcm = audio.audio_int16_bytes
                if wav.tell() * 2 + len(pcm) > MAX_WAV_BYTES - 44:
                    return None
                wav.writeframesraw(pcm)
            if wav.tell() == 0:
                return None
        return output.getvalue()

    def reject(self, status, message):
        payload = json.dumps({"error": message}).encode("utf-8")
        self.respond(status, payload, "application/json")

    def respond(self, status, payload, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    with PiperServer() as server:
        server.serve_forever()
