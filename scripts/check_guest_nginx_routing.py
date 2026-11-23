"""Exercise the opt-in guest Nginx routes with an isolated loopback proxy and stub."""

import http.client
import pathlib
import subprocess
import tempfile
import time
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "nginx:1.28-alpine"
PILOT = "pilot.example.test"
IP_HOST = "192.0.2.10"
NGINX_CONFIG = """
events {}
http {
    include /hotel-ai/hotel-ai-guest-zones.conf;
    limit_req_zone $binary_remote_addr zone=hotel_ai_chat:1m rate=60r/m;
    limit_req_zone $binary_remote_addr zone=hotel_ai_audio:1m rate=30r/m;
    server {
        listen 80 default_server;
        server_name 192.0.2.10;
        include /hotel-ai/hotel-ai-reception.inc;
    }
    server {
        listen 80;
        server_name pilot.example.test;
        include /hotel-ai/hotel-ai-reception.inc;
        include /hotel-ai/hotel-ai-guest.inc;
    }
    server {
        listen 127.0.0.1:8080;
        location / {
            return 200 "$request_method $request_uri $host";
        }
    }
}
"""


def docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=180, **kwargs
    )


def request(port, host, method, path, body=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        client.request(method, path, body, {"Host": host})
        response = client.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        client.close()


def wait_for_proxy(port):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if request(port, IP_HOST, "GET", "/healthz/")[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.2)
    raise AssertionError("The isolated Nginx health route did not become ready")


def verify_routes(port):
    routes = [
        ("GET", "/guest/session/"),
        ("POST", "/guest/google/nonce/"),
        ("POST", "/guest/google/sign-in/"),
        ("POST", "/guest/sign-out/"),
    ]
    for method, path in routes:
        assert request(port, IP_HOST, method, path)[0] == 404, path
        assert request(port, "unknown.example.test", method, path)[0] == 404, path
        status, body = request(port, PILOT, method, path)
        assert (status, body) == (200, f"{method} {path} {PILOT}"), (path, status, body)
        # The slashless form must neither redirect nor reach the backend.
        assert request(port, PILOT, method, path.rstrip("/"))[0] == 404, path

    for path in ("/guest/", "/guest/google/extra/", "/guest/sign-out/extra/"):
        assert request(port, PILOT, "GET", path)[0] == 404, path

    sign_in = "/guest/google/sign-in/"
    assert request(port, PILOT, "POST", sign_in, b"x" * 4225)[0] == 413
    assert request(port, PILOT, "POST", sign_in, b"x" * 4224)[0] == 200

    statuses = [request(port, PILOT, "GET", "/guest/session/")[0] for _ in range(8)]
    assert 429 in statuses and all(status in (200, 429) for status in statuses), statuses
    assert request(port, PILOT, "GET", "/chat/")[0] == 200
    assert request(port, IP_HOST, "GET", "/voice/")[0] == 200


def main():
    name = f"hotel-ai-guest-nginx-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory() as temp:
        conf = pathlib.Path(temp) / "nginx.conf"
        conf.write_text(NGINX_CONFIG, encoding="utf-8")
        try:
            start = docker(
                "run", "-d", "--name", name,
                "-p", "127.0.0.1::80",
                "-v", f"{ROOT / 'deploy' / 'nginx'}:/hotel-ai:ro",
                "-v", f"{conf}:/etc/nginx/nginx.conf:ro",
                IMAGE,
            )
            if start.returncode:
                raise RuntimeError(start.stderr)
            binding = docker("port", name, "80/tcp", check=True).stdout.strip()
            port = int(binding.rsplit(":", 1)[1])
            wait_for_proxy(port)
            verify_routes(port)
        except Exception:
            logs = docker("logs", "--tail", "30", name)
            print(logs.stderr)
            raise
        finally:
            docker("rm", "-f", name)

    print("Verified guest route allowlist, host isolation, request limits and reception access.")


if __name__ == "__main__":
    main()
