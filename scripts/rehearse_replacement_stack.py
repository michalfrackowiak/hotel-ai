"""Restore a generated fictional SQLite backup and exercise isolated cached Docker images."""

import argparse
import json
import re
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4


FIXTURE = """
import sqlite3
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.utils import timezone

from bookings.models import Price, Room
from bookings.services import confirm_booking

room = Room.objects.create(code="OFFHOST-FIXTURE", capacity=2)
Price.objects.create(room=room, nightly_rate="137.00")
arrival = timezone.localdate() + timedelta(days=30)
confirm_booking(
    room_id=room.pk, check_in=arrival, check_out=arrival + timedelta(days=2), guests=2,
    name="Fictional rehearsal guest", email="rehearsal@example.test", nightly_rate=Decimal("137.00"),
    idempotency_key=UUID("deadbeef-e89b-42d3-a456-426614174000"), delivery_method="email",
)
with sqlite3.connect("/app/data/db.sqlite3") as source:
    with sqlite3.connect("/app/data/snapshot.sqlite3") as snapshot:
        source.backup(snapshot)
"""
VERIFY_FIXTURE = """
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from bookings.models import Booking

booking = Booking.objects.select_related("room", "room__price", "client").get(
    idempotency_key=UUID("deadbeef-e89b-42d3-a456-426614174000"),
)
if (
    booking.room.code != "OFFHOST-FIXTURE"
    or booking.room.price.nightly_rate != Decimal("137.00")
    or booking.nightly_rate != Decimal("137.00")
    or booking.guests != 2
    or booking.check_out - booking.check_in != timedelta(days=2)
    or booking.client.email != "rehearsal@example.test"
):
    raise RuntimeError("Fictional booking did not survive recovery.")
print("Restored fictional booking, room and nightly rate")
"""
DIGEST = "import hashlib; print(hashlib.file_digest(open('/backup/snapshot.sqlite3', 'rb'), 'sha256').hexdigest())"
SECRET = "DJANGO_SECRET_KEY=fictional-offhost-rehearsal-only"
HTTP_READY = "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).close()"
HTTP_PAGES = """
import urllib.request

origin = "http://127.0.0.1:8000"
for path in ("/chat/?lang=en", "/voice/?lang=pl", "/admin/login/", "/static/admin/css/base.css"):
    with urllib.request.urlopen(origin + path, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Unhealthy restored route: {path}")
print("Restored chat, voice, admin login and admin CSS returned HTTP 200")
"""


def require_windows_memory():
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32), ("load", ctypes.c_uint32),
            ("total_physical", ctypes.c_uint64), ("available_physical", ctypes.c_uint64),
            ("total_page_file", ctypes.c_uint64), ("available_page_file", ctypes.c_uint64),
            ("total_virtual", ctypes.c_uint64), ("available_virtual", ctypes.c_uint64),
            ("available_extended_virtual", ctypes.c_uint64),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    get_memory_status = ctypes.windll.kernel32.GlobalMemoryStatusEx
    get_memory_status.argtypes = (ctypes.POINTER(MemoryStatus),)
    get_memory_status.restype = wintypes.BOOL
    if not get_memory_status(ctypes.byref(status)):
        raise SystemExit("Windows available physical memory could not be measured.")
    available_mib = status.available_physical // (1024 ** 2)
    if available_mib < 2048:
        raise SystemExit(
            f"Windows has {available_mib} MiB of available physical memory. "
            "Use a quiet host with at least 2 GiB available for this rehearsal."
        )


def docker(*args, capture=False, check=True):
    return subprocess.run(
        ["docker", *args], text=True, capture_output=capture, check=check, timeout=120,
    )


def run_flags(network, memory):
    return ("--pull=never", "--network", network, "--memory", memory, "--cpus", "1", "--init")


def wait_http(probe_container, service_container, url):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        if docker("exec", probe_container, "python", "-c", HTTP_READY, url, capture=True, check=False).returncode == 0:
            return
        sleep(1)
    docker("logs", "--tail", "30", service_container)
    raise RuntimeError(f"Service did not become ready: {url}")


def remove_resources(label, containers, network, volumes):
    removals = (("rm", "-f", *containers), ("network", "rm", network), ("volume", "rm", *volumes))
    for removal in removals:
        try:
            docker(*removal, capture=True, check=False)
        except (subprocess.TimeoutExpired, OSError):
            # The daemon may have processed the request before the client timed out.
            pass

    unverified = []
    for resource, option in (("container", "-aq"), ("network", "-q"), ("volume", "-q")):
        try:
            remaining = docker(resource, "ls", option, "--filter", f"label={label}", capture=True).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            unverified.append(resource)
        else:
            if remaining:
                unverified.append(resource)
    if unverified:
        raise RuntimeError(f"Rehearsal cleanup could not be verified ({', '.join(unverified)}); filter by label {label}.")
    print("Rehearsal containers, network and volumes removed.", flush=True)


def rehearse(web_image, piper_image, asr_image):
    require_windows_memory()
    web, piper, asr = json.loads(docker("image", "inspect", web_image, piper_image, asr_image, capture=True).stdout)
    prefix = f"hotel-ai-offhost-{uuid4().hex[:12]}"
    label = f"hotel-ai.offhost-rehearsal={prefix}"
    web_name, piper_name, asr_name, job = (f"{prefix}-{role}" for role in ("web", "piper", "asr", "job"))
    source, restore = (f"{prefix}-{role}" for role in ("source", "restore"))
    backup_mount = f"{source}:/backup:ro"
    fixture_job = ("run", "--rm", "--name", job, "--label", label, *run_flags("none", "256m"))
    started = monotonic()
    for role, image in (("web", web), ("piper", piper), ("asr", asr)):
        print(f"{role}: {image['Os']}/{image['Architecture']} {image['Id']}", flush=True)
    print(f"Rehearsal resources: {prefix}", flush=True)
    try:
        docker("volume", "create", "--label", label, source)
        docker("volume", "create", "--label", label, restore)
        docker("network", "create", "--internal", "--label", label, prefix)
        docker(
            *fixture_job, "-e", SECRET, "-v", f"{source}:/app/data", web["Id"],
            "sh", "-ec", 'python manage.py migrate --noinput >/dev/null; python manage.py shell -c "$1"',
            "fixture", FIXTURE,
        )
        digest_cmd = (*fixture_job, "-v", backup_mount, web["Id"], "python", "-c", DIGEST)
        source_digest = docker(*digest_cmd, capture=True).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", source_digest):
            raise RuntimeError("Expected a valid SHA-256 digest for the source snapshot.")
        try:
            docker(
                *fixture_job, "-v", backup_mount, "-v", f"{restore}:/app/data", web["Id"],
                "sh", "-ec", "mkdir /app/data/restore; python scripts/verify_recovery.py "
                '--backup /backup/snapshot.sqlite3 --restore-dir /app/data/restore --expected-sha256 "$1"; '
                "mv /app/data/restore/restored.sqlite3 /app/data/db.sqlite3",
                "restore-fixture", source_digest,
            )
        except subprocess.SubprocessError:
            raise RuntimeError("Replacement snapshot restore verification failed.") from None
        require_windows_memory()
        for runtime, name, alias, cap in ((piper, piper_name, "piper", "384m"), (asr, asr_name, "asr", "768m")):
            docker(
                "run", "-d", "--name", name, "--label", label, *run_flags(prefix, cap),
                "--read-only", "--network-alias", alias, runtime["Id"],
            )
        docker(
            "run", "-d", "--name", web_name, "--label", label, *run_flags(prefix, "256m"),
            "-v", f"{restore}:/app/data", "-e", SECRET, "-e", "DJANGO_DEBUG=0",
            "-e", "HOTEL_PIPER_URL=http://piper:5000", "-e", "HOTEL_ASR_URL=http://asr:5001",
            web["Id"], "gunicorn", "config.wsgi:application", "--bind", "127.0.0.1:8000",
            "--workers", "1", "--threads", "4",
        )
        wait_http(web_name, web_name, "http://127.0.0.1:8000/healthz/")
        wait_http(web_name, piper_name, "http://piper:5000/healthz")
        wait_http(web_name, asr_name, "http://asr:5001/healthz")
        docker("exec", web_name, "python", "-c", HTTP_PAGES)
        docker("exec", web_name, "python", "manage.py", "shell", "-c", VERIFY_FIXTURE)
        docker("exec", web_name, "python", "scripts/smoke_piper.py", "http://127.0.0.1:8000", "--asr")
        if docker(*digest_cmd, capture=True).stdout.strip() != source_digest:
            raise RuntimeError("Source snapshot changed after runtime checks.")
        print(f"Source checksum unchanged. Rehearsal checks passed in {monotonic() - started:.2f}s.", flush=True)
    finally:
        remove_resources(label, (job, web_name, piper_name, asr_name), prefix, (source, restore))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--piper-image", required=True)
    parser.add_argument("--asr-image", required=True)
    images = parser.parse_args()
    rehearse(images.web_image, images.piper_image, images.asr_image)
