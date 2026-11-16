# Speech staging capacity, 16 November 2026

This second version 0.4 increment measures the existing speech images on the ARM64 server using temporary isolated containers. [Raw results](staging-capacity-results.json) were captured at 10:12 Warsaw time. Production container IDs, start times and healthy status were identical before and after. Temporary containers and their internal network were removed. No guest data, host ports, data mounts or production network attachments were used.

## Method

Run [benchmark_capacity.py](../scripts/benchmark_capacity.py) with [benchmark_speech.py](../scripts/benchmark_speech.py) alongside it on the Linux Docker host. The operator needs Docker administration and cgroup v2 read access:

```sh
sudo python3 benchmark_capacity.py --rounds 3 > staging-capacity.json
```

The tool requires healthy production services and at least 5.5 GiB available RAM: 2.5 GiB staging caps plus 3 GiB reserve. It reuses the running services' image IDs, creates uniquely labeled containers on an internal network, drops capabilities, uses a read-only filesystem and disables privilege escalation. Each staging speech worker is capped at one CPU; Piper at 1 GiB and ASR at 1.5 GiB with no additional swap allowance. Cleanup verifies ownership labels before removal. Do not interrupt with SIGKILL; the normal signal/error cleanup cannot run after forced process termination.

Five baseline web-health requests precede creation. Readiness timing starts immediately before `docker start`, and ends at the first successful private health response. This includes process/model loading, but excludes image download, image creation and uncached disk behavior. Host page cache was already warm.

Piper generates one short fictional duration per language. Each engine then receives three pairs of synchronized requests in English and three pairs in Polish. Engines and pairs run sequentially; this is an admission test, not a full multi-user booking load test. The web monitor samples only the loopback health route. Counters are read from each temporary container's cgroup v2.

## Results

| Measurement | Piper | Vosk |
| --- | --- | --- |
| Start to ready | 1.597 s | 1.739 s |
| Overlap pairs | 6 | 6 |
| HTTP 200 results | 6 | 6 |
| HTTP 429 busy results | 6 | 6 |
| Cgroup lifetime memory peak | 257.84 MiB | 320.63 MiB |
| Memory max/OOM events | 0 | 0 |

Every pair produced one success and one busy response. The bounded semaphore therefore provides one inference slot per engine across both languages, with no waiting inference queue. These figures do not justify increasing concurrency. Cgroup memory peak is for this short staging process lifetime; it does not replace the higher observed long-running production Piper footprint or predict long-utterance memory use.

The 35 web-health samples during staging had zero errors, p50 10.1 ms, p95 17.6 ms and maximum 30.2 ms. This confirms health-route responsiveness during this run, not availability/booking transaction latency or public HTTPS performance. ASR recorded two CPU-throttled periods totaling 20.915 ms; resource counters include startup and fixture preparation as well as overlap rounds.

## Capacity decision

Retain the current one-CPU, one-inference-per-worker setup and bilingual sharing. A provider switch should reserve measured resources for both the replacement and any still-referenced shared provider, keeping at least 3 GiB available for other work. The 1/1.5 GiB service limits remain provisional conservative caps; this short test provides no reason to lower them.

Before opening admission beyond one request, implement a bounded queue with request deadlines/cancellation and clear waiting/busy feedback. Initial proposed policy for evaluation: one queued request per engine, maximum three seconds waiting, then a recoverable busy response. This is a proposal, not a deployed queue or proven capacity. Measure both languages together, long replies, web booking transactions, and repeated sessions before promotion.

Version 0.4 remains open for sustained and mixed-engine load, uncached cold start, actual microphone/accent/noise evaluation, full off-host service restore and any separately used PC/macOS inventory. See [recognition findings](speech-recognition-findings.md) for contact correctness follow-up.
