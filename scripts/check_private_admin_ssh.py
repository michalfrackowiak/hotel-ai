"""Verify a temporary Gunicorn fixture through SSH and from outside the host."""
import argparse
from http.cookiejar import CookieJar
import ipaddress
import re
import secrets
import socket
import subprocess
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def check_admin(base, password, process):
    cookies = CookieJar()
    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))
    login = base + "/admin/login/?next=/admin/"
    deadline = monotonic() + 45
    while monotonic() < deadline and process.poll() is None:
        try:
            with client.open(base + "/healthz/", timeout=2) as response:
                if response.status == 200:
                    break
        except (OSError, URLError):
            sleep(0.5)
    else:
        raise RuntimeError("The forwarded fixture never became ready.")
    with client.open(login, timeout=4) as response:
        if response.status != 200:
            raise RuntimeError("Fixture login page failed.")
    csrf = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
    fields = {"username": "cutover-fixture-staff", "password": password,
              "csrfmiddlewaretoken": csrf, "next": "/admin/"}
    request = Request(login, data=urlencode(fields).encode(), headers={"Origin": base, "Referer": login})
    with client.open(request, timeout=6) as response:
        if response.geturl() != base + "/admin/" or b"Log out" not in response.read(65537):
            raise RuntimeError("The fixture staff login did not reach the authenticated admin.")
    print("SSH-forwarded Django admin login and CSRF passed.", flush=True)


def check_public(address, port):
    try:
        with socket.create_connection((address, port), timeout=3):
            raise RuntimeError("The staging Gunicorn port is reachable directly.")
    except (ConnectionRefusedError, TimeoutError):
        pass
    client = build_opener(ProxyHandler({}))
    try:
        with client.open(f"https://{address}/admin/", timeout=5):
            raise RuntimeError("The public HTTPS endpoint exposed admin.")
    except HTTPError as error:
        with error:
            if error.code != 404 or error.url != f"https://{address}/admin/":
                raise RuntimeError("The public HTTPS admin route did not return 404.") from None
    print("Direct staging port was unreachable and public HTTPS admin returned 404.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", type=ipaddress.IPv4Address, required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # The remote command contains only the expected private archive path and a decimal port.
    if not re.fullmatch(r"/tmp/hotel-ai-cutover\.[a-zA-Z0-9]{8}/source", args.source) or not 20000 <= args.port <= 59999:
        parser.error("Supply an isolated source directory and a fixture port from 20000 to 59999.")
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        local = reserve.getsockname()[1]
    password = secrets.token_hex(32)
    command = ["ssh", "-T", "-i", args.key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o",
               "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
               "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR", "-L",
               f"127.0.0.1:{local}:127.0.0.1:{args.port}", f"ubuntu@{args.address}",
               f"sh {args.source}/scripts/check_private_admin_listener.sh {args.port} {args.source}"]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            process.stdin.write((password + "\n").encode("ascii"))
            process.stdin.close()
            check_admin(f"http://127.0.0.1:{local}", password, process)
            check_public(str(args.address), args.port)
        finally:
            try:
                if process.wait(timeout=110) != 0:
                    raise RuntimeError("The server fixture did not complete its cleanup checks.")
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("The SSH fixture exceeded its deadline.") from None


if __name__ == "__main__":
    main()
