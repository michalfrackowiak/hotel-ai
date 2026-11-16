# Sustained mixed staging evaluation

This version 0.4 increment exercises repeated booking transactions while English and Polish speech workers run concurrently. It complements the earlier [two-request admission test](staging-speech-capacity.md). It is a bounded synthetic workload, not a claim of maximum capacity or real guest recognition accuracy.

[Raw results](staging-mixed-load-results.json) were captured on 16 November 2026 at 10:52 Warsaw time, using the deployed application image for revision `a43629d607e0076824bc4e65d9ce11d722ad8bfd`. The executed runner SHA-256 was `35c739fc5b297ead56d609a52521025706e17fbdab0ab266945af76c5ff26de1`; its uploaded and repository copies matched. Image IDs, conditions, individual timings and resource counters are recorded in the JSON.

## Method and isolation

Run [benchmark_mixed_load.py](../scripts/benchmark_mixed_load.py) on the Linux Docker host with `benchmark_capacity.py` and `benchmark_speech.py` in the same directory. It requires Docker administration and cgroup v2 counter access:

```sh
sudo python3 benchmark_mixed_load.py --duration 60 > staging-mixed-load-results.json
```

The permitted load duration is 60–120 seconds, excluding setup and cleanup. New work stops at the deadline; an already running request may finish afterward. A failed production-health preflight prevents staging creation. Two consecutive health failures stop new workload; any observed production-health error makes the report fail. Normal interruption also triggers cleanup, but SIGKILL or host failure cannot run Python cleanup.

The benchmark reuses the running application's and speech engines' image IDs in temporary containers. The web container receives a fresh temporary SQLite database, demo rooms, fictional contacts, and no production environment or credentials. Speech requests and booking requests use the private staging network. No host ports or production data volumes are attached. Confirmation records remain pending in the isolated database; no delivery worker runs.

Each staging container has a half-CPU limit. The combined memory limits are 3 GiB, and preflight requires at least 6 GiB of available RAM. These CPU limits are deliberately lower than the production speech limits, so results characterize this test configuration rather than production capacity. Production health is monitored, and temporary resources are removed after the run. Before/after production container identity and start-time comparisons detect unexpected restarts.

The workload uses one sequential booking client and paired simultaneous requests to each speech engine. The engine loops run concurrently, with one second between pairs; booking sessions have a three-second pause. The clients alternate English and Polish, and speech pairs cycle through short and longer synthetic fixtures. Booking sessions exercise dates, room selection, stay consent, contact readback and final approval against distinct fictional stays. The speech streams run alongside these sessions; they do not simulate microphone capture, browser playback or network latency from a guest device.

## Results

The configured 60-second workload finished its last in-flight request after 61.07 seconds. All 20 booking sessions completed (10 per language), achieving 0.328 bookings/second with the deliberate three-second pacing. The temporary database contained exactly 20 clients, 20 bookings and 20 pending confirmations, with no other confirmation status and no administrator accounts. These are staging writes only; all disappeared with the temporary container.

| Measurement | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: |
| Complete scripted booking session, 20 sessions | 91.1 ms | 105.5 ms | 227.7 ms |
| Booking JSON turn, 190 requests | 5.6 ms | 38.2 ms | 88.1 ms |
| Final contact approval/booking transaction, 20 requests | 37.7 ms | 43.4 ms | 44.8 ms |
| Production health route, 163 samples | 9.2 ms | 16.7 ms | 34.2 ms |

Session timing includes the initial page and scripted turns sent without human thinking time; it is not guest time-to-book. Production health sampling includes staging startup and fixture preparation as well as the mixed-load window. All health responses succeeded. The report's nearest-rank p95 for six or seven speech samples is the maximum, so treat it as a small-sample observation.

| Successful speech request | Piper p95 | Vosk p95 | Samples (Piper / Vosk) |
| --- | ---: | ---: | ---: |
| English short, 1.39 s audio | 0.492 s | 1.247 s | 7 / 6 |
| Polish short, 1.43 s audio | 0.492 s | 0.825 s | 7 / 6 |
| English longer, 4.14 s audio | 1.502 s | 2.317 s | 7 / 6 |
| Polish longer, 7.70 s audio | 2.718 s | 2.013 s | 6 / 6 |

Piper received 54 requests: 27 succeeded and 27 returned HTTP 429. Vosk received 48: 24 succeeded and 24 returned HTTP 429. Each pair produced one success and one busy result. Busy responses are excluded from the successful-inference latency table; the raw report retains them. Inference remains a single shared slot per engine across both languages.

| Resource | Piper | Vosk | Web |
| --- | ---: | ---: | ---: |
| Process-to-ready with cached image | 3.04 s | 3.28 s | 7.73 s |
| Cgroup lifetime peak memory | 438.3 MiB | 333.8 MiB | 69.8 MiB |
| CPU throttled time | 18.85 s | 19.15 s | 4.28 s |
| Memory limit/OOM events | 0 | 0 | 0 |

Web readiness includes migrations and demo seeding. Resource counters cover each container's lifetime, including startup and fixture preparation, not just the timed load. CPU throttling is expected under the deliberately restrictive half-CPU limits. The benchmark exited successfully, all four labeled resources were removed, and production container IDs, start times and healthy states matched before and after.

## Interpretation and next action

Separate booking completion latency, request latency, speech latency and health-route responsiveness. Report the achieved throughput of this paced workload; do not interpret a successful run as a concurrency or saturation limit. Process readiness uses cached images and the existing host page cache. A one-minute run also does not establish long-term memory stability.

Keep one inference per engine until a bounded queue and deadline/cancellation behavior are evaluated. Physical microphone, accent/noise, uncached startup, and off-host recovery checks remain separate gates in [the roadmap](roadmap.md).

The remaining speech bottleneck is admission: half of simultaneous synthetic requests were rejected despite responsive web transactions. This supports evaluating one waiting request per engine with a short deadline and clear busy feedback before raising inference concurrency. The 2.718-second Polish longer TTS result exceeds the provisional two-second target under this half-CPU setup; it does not establish a breach at the deployed one-CPU limit. Keep current production resource limits and measure the queued workload separately. No runtime change or deployment accompanies this report.
