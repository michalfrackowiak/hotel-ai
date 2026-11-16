"""Measure isolated speech-container startup and two-request admission on Linux.

Run with sudo on the Docker host, alongside benchmark_speech.py. Existing speech
images are reused. No ports, volumes, production networks, or guest data are used.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import signal
import subprocess
from threading import Barrier, Event, Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

from benchmark_speech import MAX_WAV_BYTES, NoRedirect, pcm_from_wav, percentile, post


LABEL = "hotel-ai.capacity-probe"
PRODUCTION = ("hotel-ai-web-1", "hotel-ai-piper-1", "hotel-ai-asr-1")


def docker(*arguments):
    return subprocess.check_output(["docker", *arguments], text=True, timeout=30).strip()


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def health(url):
    start = monotonic()
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=2) as response:
            response.read(1024)
        return {"seconds": monotonic() - start, "status": 200}
    except (OSError, HTTPException, ValueError) as error:
        return {"seconds": monotonic() - start, "status": getattr(error, "code", None), "error": type(error).__name__}


def cgroup_path(pid):
    for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            return Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    raise RuntimeError("The benchmark requires cgroup v2 resource counters.")


def counters(path):
    result = {name: int((path / name).read_text()) for name in ("memory.current", "memory.peak")}
    for name in ("cpu.stat", "memory.events"):
        result[name] = {key: int(value) for key, value in (line.split() for line in (path / name).read_text().splitlines())}
    return result


def production_state():
    return {
        name: {
            "id": item["Id"], "started_at": item["State"]["StartedAt"],
            "running": item["State"]["Running"], "health": item["State"].get("Health", {}).get("Status"),
        }
        for name in PRODUCTION for item in [inspect(name)]
    }


def wait_ready(name, network, port):
    started = monotonic()
    docker("start", name)
    details = inspect(name)
    base = "http://" + details["NetworkSettings"]["Networks"][network]["IPAddress"] + f":{port}"
    while monotonic() - started < 90:
        if health(base + "/healthz")["status"] == 200:
            return base, monotonic() - started, cgroup_path(details["State"]["Pid"])
        if not inspect(name)["State"]["Running"]:
            raise RuntimeError("Staging container exited before readiness.")
        sleep(0.25)
    raise RuntimeError("Staging speech service did not become ready within 90 seconds.")


def paired_request(url, payload, content_type, response_type, limit, language):
    barrier = Barrier(2)

    def execute():
        barrier.wait(timeout=5)
        started = monotonic()
        try:
            body, elapsed = post(
                build_opener(ProxyHandler({}), NoRedirect()), url, payload,
                content_type, response_type, limit, 20,
            )
            result = {"status": 200, "seconds": elapsed}
            if response_type == "application/json":
                result["transcript"] = json.loads(body)["text"]
            return result
        except HTTPError as error:
            return {"status": error.code, "seconds": monotonic() - started}
        except (OSError, HTTPException, ValueError) as error:
            return {"status": None, "seconds": monotonic() - started, "error": type(error).__name__}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(execute) for _ in range(2)]
        return {"language": language, "requests": [future.result() for future in futures]}


def run(args):
    token = uuid4().hex
    network = "hotel-capacity-" + token
    containers = []
    stop = Event()
    samples = []
    report = {
        "schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(), "run_id": token,
        "conditions": {
            "rounds_per_engine_per_language": args.rounds, "simultaneous_requests": 2,
            "image_download_included": False, "health_url": args.web_health_url,
            "limitations": [
                "Readiness starts immediately before docker start and includes process/model loading with existing host page cache.",
                "Synthetic fixtures and two simultaneous requests measure admission behavior, not real guest accuracy or sustainable throughput.",
                "Rounds and engines run sequentially; only one engine is loaded with concurrent requests at a time.",
                "Health latency measures the loopback web health route, excluding booking database work and public network latency.",
                "No production container is restarted or attached to staging. Staging has no host ports or data mounts.",
            ],
        },
        "production_before": production_state(), "startup": {}, "load": {},
        "cleanup": {},
    }
    baseline = [health(args.web_health_url) for _ in range(5)]
    report["web_health_baseline"] = baseline
    if any(item["status"] != 200 for item in baseline) or any(
        not item["running"] or item["health"] != "healthy" for item in report["production_before"].values()
    ):
        report["error"] = "UnhealthyProductionPreflight"
        report["production_after"] = production_state()
        report["web_health_during_staging"] = {"errors": 0, "samples": 0}
        return report

    def monitor():
        while not stop.is_set():
            samples.append(health(args.web_health_url))
            stop.wait(0.25)

    worker = Thread(target=monitor, daemon=True)
    worker.start()
    try:
        docker("network", "create", "--internal", "--label", f"{LABEL}={token}", network)
        services = {}
        for engine, source, memory, port in (
            ("piper", "hotel-ai-piper-1", "1g", 5000),
            ("asr", "hotel-ai-asr-1", "1536m", 5001),
        ):
            image = inspect(source)["Image"]
            name = network + "-" + engine
            containers.append(name)
            docker(
                "create", "--name", name, "--label", f"{LABEL}={token}", "--network", network,
                "--cpus=1", "--memory=" + memory, "--memory-swap=" + memory,
                "--read-only", "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges", image,
            )
            base, elapsed, group = wait_ready(name, network, port)
            services[engine] = {"base": base, "group": group}
            report["startup"][engine] = {
                "image_id": image, "process_to_ready_seconds": elapsed,
                "cpu_limit": 1, "memory_limit": memory, "resources_ready": counters(group),
            }
        # Generate fictional guest audio before overlap rounds. This warms Piper.
        audio = {}
        for language, text in (("en", "for three nights"), ("pl", "na trzy noce")):
            wav, _ = post(
                build_opener(ProxyHandler({}), NoRedirect()), services["piper"]["base"] + "/synthesize",
                json.dumps({"language": language, "text": text}).encode(), "application/json", "audio/wav",
                MAX_WAV_BYTES, 20,
            )
            audio[language], _ = pcm_from_wav(wav)
        for engine in ("piper", "asr"):
            rounds = []
            for language, text in (("en", "for three nights"), ("pl", "na trzy noce")):
                for index in range(args.rounds):
                    if engine == "piper":
                        url = services[engine]["base"] + "/synthesize"
                        payload = json.dumps({"language": language, "text": text}).encode()
                        request_type, response_type, limit = "application/json", "audio/wav", MAX_WAV_BYTES
                    else:
                        url = services[engine]["base"] + "/transcribe?language=" + language
                        payload = audio[language]
                        request_type, response_type, limit = "application/octet-stream", "application/json", 8192
                    result = paired_request(url, payload, request_type, response_type, limit, language)
                    rounds.append({"round": index + 1, **result})
            report["load"][engine] = rounds
        report["resources_final"] = {engine: counters(item["group"]) for engine, item in services.items()}
    except Exception as error:
        report["error"] = type(error).__name__
    finally:
        stop.set()
        worker.join(timeout=3)
        report["web_health_during_staging"] = {
            "samples": len(samples), "errors": sum(item["status"] != 200 for item in samples),
            "p50_seconds": percentile([item["seconds"] for item in samples], 0.5),
            "p95_seconds": percentile([item["seconds"] for item in samples], 0.95),
            "max_seconds": max((item["seconds"] for item in samples), default=None),
        }
        for name in reversed(containers):
            try:
                if inspect(name)["Config"]["Labels"].get(LABEL) == token:
                    docker("rm", "-f", name)
                    report["cleanup"][name] = "removed"
            except Exception as error:
                report["cleanup"][name] = type(error).__name__
        try:
            details = json.loads(docker("network", "inspect", network))[0]
            if details["Labels"].get(LABEL) == token:
                docker("network", "rm", network)
                report["cleanup"][network] = "removed"
        except Exception as error:
            report["cleanup"][network] = type(error).__name__
        report["production_after"] = production_state()
    return report


def failed(report):
    invalid_pair = any(
        not any(request["status"] == 200 for request in pair["requests"])
        or any(request["status"] not in (200, 429) for request in pair["requests"])
        for rounds in report["load"].values() for pair in rounds
    )
    return (
        "error" in report or invalid_pair or report["production_before"] != report["production_after"]
        or report["web_health_during_staging"]["errors"] > 0
        or any(value != "removed" for value in report["cleanup"].values())
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--web-health-url", default="http://127.0.0.1:8080/healthz/")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo on the Linux Docker host to read container cgroup counters.")
    available = next(
        int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if available < int(5.5 * 1024**3):
        parser.error("Keep at least 3 GiB available beyond the staging containers' combined 2.5 GiB caps.")
    def interrupted(*_):
        raise InterruptedError()

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    report = run(args)
    report["preflight_memory_available_bytes"] = available
    print(json.dumps(report, indent=2))
    return int(failed(report))


if __name__ == "__main__":
    raise SystemExit(main())
