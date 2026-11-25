# Routed pilot mixed load

This benchmark runs fictional bookings alongside English and Polish speech on the ARM64 host. It extends the [two-minute queue check](queue-load-validation.md) to ten minutes and uses images from the named routed pilot primary containers. The workload exercises the staged speech adapters directly. Controller routing and dispatch are outside the benchmark.

## Method

Run the following from a checkout on the Linux Docker host. The routed profile expects the pilot web container and its two primary workers. It uses the local Docker socket and checks loopback web health, current CPU limits, available memory and the running container set before creating staging resources.

```sh
sudo python3 scripts/benchmark_mixed_load.py --routed-pilot --duration 600 > routed-pilot-mixed-load.json
```

Each of three staging containers has a 0.25-core CPU cap. With 1.05 cores allocated to production on a two-core host, the combined cap was 1.80 cores. The staging memory caps are 512 MiB for Django, 1 GiB for Piper and 1.5 GiB for Vosk. The runner gives staging its own internal Docker network and puts its SQLite database in tmpfs. The app uses fictional contacts and creates pending confirmation records. No delivery worker runs.

One client completes scripted bookings while each speech engine receives pairs of simultaneous requests. The workload alternates between English and Polish and uses both short and longer speech fixtures. Direct speech requests measure the adapters, while booking requests exercise Django's HTTP session, CSRF and booking transaction. The runner stops scheduling new work after two consecutive failed production health checks. Any production health failure, unexpected container change or staging cleanup failure fails the run.

## 19 November 2026 results

The 600-second profile ran for 601.90 seconds including in-flight work. All 188 fictional bookings completed, with 188 clients and pending confirmations in staging SQLite. The database contained zero other confirmation records and zero admin accounts.

| Service | Cached startup | HTTP 200 | HTTP 429 busy | Successful request p95 | Peak memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| Piper | 5.79 s | 219 | 41 | 6.08 s | 504.4 MiB |
| Vosk | 6.32 s | 170 | 56 | 5.16 s | 336.9 MiB |
| Django | 16.25 s | | | | 72.0 MiB |

P95 uses nearest rank and includes request waiting time. Both speech services were frequently CPU-throttled at the staging cap. No cgroup memory limit or OOM events were recorded. All 1,259 sampled production health requests returned HTTP 200. The seven production containers matched their initial identity, image, start time, CPU cap and health snapshots. The runner removed the three staging containers and their network.

The run provides a ten-minute resource and throughput baseline under the specified workload. Longer stability, uncached startup, browser microphone accuracy and full off-host service recovery require separate evidence.
