"""Run 60–120 seconds of fictional booking and speech load in isolated staging.

Run with sudo on the Linux Docker host alongside benchmark_capacity.py and
benchmark_speech.py. Uses existing production image IDs with fresh staging data.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import signal
from threading import Event, Thread
from time import monotonic
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

from benchmark_capacity import cgroup_path, counters, docker, health, inspect, paired_request, production_state
from benchmark_speech import MAX_WAV_BYTES, NoRedirect, pcm_from_wav, post


LABEL = "hotel-ai.mixed-probe"
FIXTURES = (
    ("en", "short", "for three nights"),
    ("pl", "short", "na trzy noce"),
    ("en", "long", "I would like to arrive tomorrow and stay for three nights with two guests please."),
    ("pl", "long", "Chcemy przyjechać jutro i zostać na trzy noce. Będą dwie osoby. Proszę sprawdzić dostępność."),
)


def memory_available():
    return next(
        int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )


def json_turn(client, base, token, payload):
    started = monotonic()
    request = Request(
        base + "/reception/message/", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-CSRFToken": token, "Origin": base},
    )
    with client.open(request, timeout=10) as response:
        body = response.read(32769)
    if len(body) > 32768:
        raise ValueError("Booking response exceeded limit.")
    return json.loads(body), monotonic() - started


def ready(name, network, port, abort):
    started = monotonic()
    docker("start", name)
    details = inspect(name)
    base = "http://" + details["NetworkSettings"]["Networks"][network]["IPAddress"] + f":{port}"
    route = "/healthz/" if port == 8000 else "/healthz"
    while not abort.is_set() and monotonic() - started < 90:
        if health(base + route)["status"] == 200:
            return base, monotonic() - started, cgroup_path(details["State"]["Pid"])
        if not inspect(name)["State"]["Running"]:
            break
        abort.wait(0.25)
    raise RuntimeError("Staging readiness failed or production health gate aborted setup.")


def booking_session(base, number, language, abort, deadline):
    cookies = CookieJar()
    client = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(cookies))
    row = {"number": number, "language": language, "turn_seconds": [], "turn_stages": []}
    started = monotonic()
    try:
        if abort.is_set() or monotonic() >= deadline:
            row["abandoned_at_deadline"] = True
            return row
        with client.open(base + "/?lang=" + language, timeout=10) as response:
            response.read(65536)
        token = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
        turn, elapsed = json_turn(client, base, token, {"language": language, "start": True})
        row["turn_seconds"].append(elapsed)
        row["turn_stages"].append(turn["stage"])
        # Each session has its own room dates, preventing overlap with prior fixtures.
        arrival = date.today() + timedelta(days=30 + 4 * number)
        answers = (
            (arrival.isoformat(), "check_out"), ((arrival + timedelta(days=2)).isoformat(), "guests"),
            ("2", "room"), ("1", "confirm"), ("yes" if language == "en" else "tak", "delivery"),
            ("email", "email"), (f"fixture{number}@example.test", "contact_confirm"),
            ("yes" if language == "en" else "tak", "completed"),
        )
        if turn["stage"] == "welcome":
            turn, elapsed = json_turn(client, base, token, {
                "language": language, "revision": turn["revision"], "timeout": True,
            })
            row["turn_seconds"].append(elapsed)
            row["turn_stages"].append(turn["stage"])
        for text, expected in answers:
            if abort.is_set() or monotonic() >= deadline:
                row["abandoned_at_deadline"] = True
                return row
            turn, elapsed = json_turn(client, base, token, {
                "language": language, "revision": turn["revision"], "message": text,
            })
            row["turn_seconds"].append(elapsed)
            row["turn_stages"].append(turn["stage"])
            if turn["stage"] != expected:
                raise ValueError("Unexpected staging booking transition.")
        row["completed"] = True
    except Exception as error:
        row["error"] = type(error).__name__
    finally:
        row["seconds"] = monotonic() - started
    return row


def failed(report):
    completed = sum(row.get("completed", False) for row in report["bookings"])
    database = report.get("ephemeral_database", {})
    return bool(
        report.get("error") or report.get("production_before") != report.get("production_after")
        or report.get("production_health_errors", 0)
        or any(value != "removed" for value in report["cleanup"].values())
        or report.get("aborted_on_health")
        or not all(any(row.get("completed") and row["language"] == locale for row in report["bookings"]) for locale in ("en", "pl"))
        or any("error" in row for row in report["bookings"])
        or any(request["status"] not in (200, 429) for rounds in report["speech"].values()
               for row in rounds for request in row["requests"])
        or any(
            not any(
                request["status"] == 200
                for row in rounds if row["language"] == language and row["fixture_length"] == length
                for request in row["requests"]
            )
            for rounds in report["speech"].values() for language in ("en", "pl") for length in ("short", "long")
        )
        or any(database.get(key) != completed for key in ("bookings", "clients", "pending_confirmations"))
        or database.get("other_confirmations") != 0 or database.get("admins") != 0
        or any(
            events.get(key, 0) > 0 for resource in report.get("resources_final", {}).values()
            for events in [resource["memory.events"]] for key in ("max", "oom", "oom_kill")
        )
    )


def run(args):
    token = uuid4().hex
    network = "hotel-mixed-" + token
    containers = []
    abort, monitor_end = Event(), Event()
    health_samples = []
    report = {
        "schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "run_id": token,
        "duration_seconds": args.duration, "production_before": production_state(),
        "startup": {}, "speech": {"piper": [], "asr": []}, "bookings": [], "cleanup": {},
        "conditions": {
            "memory_caps_mib": {"web": 512, "piper": 1024, "asr": 1536}, "cpu_cap_each": 0.5,
            "speech_concurrency_per_engine": 2, "booking_session_concurrency": 1,
            "minimum_pause_between_pairs_seconds": 1, "minimum_pause_between_sessions_seconds": 3,
            "request_timeout_seconds": {"speech": 20, "booking": 10},
            "deadline_behavior": "No new session, booking turn, or speech pair starts after deadline. An in-flight request may finish afterward.",
            "limitations": [
                "Fixed fictional synthetic fixtures and typed booking turns provide no guest speech accuracy estimate.",
                "Speech uses direct internal adapters while chat exercises HTTP sessions, CSRF, and final booking writes.",
                "Cached-image startup and short bounded load do not establish saturation or long-run capacity.",
                "All staging bookings and pending notifications exist only in ephemeral tmpfs SQLite.",
            ],
        },
    }
    report["memory_available_bytes"] = memory_available()
    baseline = [health(args.web_health_url) for _ in range(3)]
    report["health_baseline"] = baseline
    if (
        report["memory_available_bytes"] < 6 * 1024**3 or any(row["status"] != 200 for row in baseline)
        or any(not row["running"] or row["health"] != "healthy" for row in report["production_before"].values())
    ):
        report["error"] = "PreflightFailed"
        report["production_after"] = production_state()
        return report

    def monitor():
        consecutive = 0
        while not monitor_end.is_set():
            row = health(args.web_health_url)
            health_samples.append(row)
            consecutive = consecutive + 1 if row["status"] != 200 else 0
            if consecutive >= 2:
                abort.set()
            monitor_end.wait(0.5)

    watcher = Thread(target=monitor, daemon=True)
    watcher.start()
    services = {}
    try:
        docker("network", "create", "--internal", "--label", f"{LABEL}={token}", network)
        for engine, memory, port in (("piper", "1g", 5000), ("asr", "1536m", 5001), ("web", "512m", 8000)):
            if abort.is_set():
                raise RuntimeError("Production health gate aborted setup.")
            name = network + "-" + engine
            image = inspect("hotel-ai-" + engine + "-1")["Image"]
            options = [
                "create", "--name", name, "--label", f"{LABEL}={token}", "--network", network,
                "--network-alias", engine, "--cpus=.5", "--memory=" + memory, "--memory-swap=" + memory,
                "--read-only", "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,uid=10001,gid=10001",
            ]
            command = []
            if engine == "web":
                options += [
                    "--tmpfs", "/app/data:rw,nosuid,nodev,noexec,size=128m,uid=10001,gid=10001",
                    "--env", "DJANGO_DEBUG=1", "--env", "DJANGO_SECRET_KEY=fictional-staging-mixed-load-only",
                    "--env", "DJANGO_ALLOWED_HOSTS=*", "--env", "DJANGO_DB_PATH=/app/data/probe.sqlite3",
                    "--env", "HOTEL_MODEL_API_KEY=", "--env", "HOTEL_PIPER_URL=",
                    "--env", "HOTEL_ASR_URL=", "--env", "HOTEL_CONFIRMATION_WAIT_EMAIL=0",
                    "--env", "HOTEL_CONFIRMATION_WAIT_SMS=0",
                    "--env", "HOTEL_PARSER_TRAINING_ENABLED=0", "--env", "HOTEL_SMTP_HOST=",
                    "--env", "HOTEL_TWILIO_AUTH_TOKEN=", "--env", "HOTEL_TWILIO_ACCOUNT_SID=",
                    "--env", "HOTEL_DEMO_ADMIN_USERNAME=", "--env", "HOTEL_DEMO_ADMIN_PASSWORD=",
                ]
                command = ["sh", "-ec", "python manage.py migrate --noinput >/dev/null && "
                           "python manage.py seed_demo >/dev/null && "
                           "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4"]
            containers.append(name)
            docker(*options, image, *command)
            base, readiness, group = ready(name, network, port, abort)
            services[engine] = {"base": base, "group": group, "name": name}
            report["startup"][engine] = {"image": image, "ready_seconds": readiness, "resources": counters(group)}
        fixtures = []
        for language, length, text in FIXTURES:
            if abort.is_set():
                raise RuntimeError("Production health gate aborted fixture setup.")
            wav, _ = post(build_opener(ProxyHandler({}), NoRedirect()), services["piper"]["base"] + "/synthesize",
                          json.dumps({"language": language, "text": text}).encode(), "application/json",
                          "audio/wav", MAX_WAV_BYTES, 20)
            pcm, duration = pcm_from_wav(wav)
            fixtures.append((language, length, text, pcm))
            report.setdefault("fixture_audio_seconds", []).append({"language": language, "length": length, "seconds": duration})
        deadline = monotonic() + args.duration

        def speech_load(engine):
            index = 0
            while not abort.is_set() and monotonic() < deadline:
                language, length, text, pcm = fixtures[index % len(fixtures)]
                synth = engine == "piper"
                url = services[engine]["base"] + ("/synthesize" if synth else "/transcribe?language=" + language)
                payload = json.dumps({"language": language, "text": text}).encode() if synth else pcm
                row = paired_request(url, payload, "application/json" if synth else "application/octet-stream",
                                     "audio/wav" if synth else "application/json", MAX_WAV_BYTES if synth else 8192, language)
                report["speech"][engine].append({"fixture_length": length, **row})
                index += 1
                abort.wait(min(1, max(0, deadline - monotonic())))

        def bookings_load():
            number = 0
            while not abort.is_set() and monotonic() < deadline:
                report["bookings"].append(booking_session(
                    services["web"]["base"], number, "en" if number % 2 == 0 else "pl", abort, deadline,
                ))
                number += 1
                abort.wait(min(3, max(0, deadline - monotonic())))

        started = monotonic()
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(speech_load, engine) for engine in ("piper", "asr")]
            jobs.append(pool.submit(bookings_load))
            try:
                for job in jobs:
                    job.result()
            except BaseException:
                abort.set()
                raise
        report["load_wall_seconds"] = monotonic() - started
        report["aborted_on_health"] = abort.is_set()
        report["resources_final"] = {engine: counters(item["group"]) for engine, item in services.items()}
        query = (
            "import json; from bookings.models import Booking,BookingConfirmation,Client; "
            "from django.contrib.auth import get_user_model; print(json.dumps({'bookings':Booking.objects.count(),"
            "'clients':Client.objects.count(),'pending_confirmations':BookingConfirmation.objects.filter(status='pending').count(),"
            "'other_confirmations':BookingConfirmation.objects.exclude(status='pending').count(),"
            "'admins':get_user_model().objects.filter(is_superuser=True).count()}))"
        )
        report["ephemeral_database"] = json.loads(docker("exec", services["web"]["name"], "python", "manage.py", "shell", "-c", query).splitlines()[-1])
        completed = sum(row.get("completed", False) for row in report["bookings"])
        report["achieved"] = {
            "completed_bookings": completed,
            "completed_bookings_per_second": completed / report["load_wall_seconds"],
            "speech_requests": {
                engine: sum(len(row["requests"]) for row in rounds) for engine, rounds in report["speech"].items()
            },
        }
    except Exception as error:
        report["error"] = type(error).__name__
    finally:
        abort.set()
        monitor_end.set()
        watcher.join(timeout=3)
        report["production_health_samples"] = health_samples
        report["production_health_errors"] = sum(row["status"] != 200 for row in health_samples)
        for name in reversed(containers):
            try:
                if inspect(name)["Config"]["Labels"].get(LABEL) != token:
                    raise RuntimeError("Resource ownership label mismatch.")
                docker("rm", "-f", name)
                report["cleanup"][name] = "removed"
            except Exception as error:
                report["cleanup"][name] = type(error).__name__
        try:
            if json.loads(docker("network", "inspect", network))[0]["Labels"].get(LABEL) != token:
                raise RuntimeError("Network ownership label mismatch.")
            docker("network", "rm", network)
            report["cleanup"][network] = "removed"
        except Exception as error:
            report["cleanup"][network] = type(error).__name__
        report["production_after"] = production_state()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, choices=range(60, 121), default=60)
    parser.add_argument("--web-health-url", default="http://127.0.0.1:8080/healthz/")
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("Run with sudo on the Linux Docker host.")

    def interrupted(*_):
        raise InterruptedError()

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    report = run(args)
    print(json.dumps(report, indent=2))
    return int(failed(report))


if __name__ == "__main__":
    raise SystemExit(main())
