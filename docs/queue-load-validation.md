# Deployed queue: two-minute staging validation

On 16 November 2026, the unchanged `benchmark_mixed_load.py --duration 120` runner exercised isolated copies of the deployed `cdea163` application/Piper/Vosk images. [Raw results](queue-validation-results.json) include image IDs, individual requests, resource counters and cleanup. The [isolation and pacing method](staging-mixed-load.md) is unchanged: fresh tmpfs SQLite, fictional guests, no production data or credentials, no host ports, half-CPU limits per container and a production-health abort gate.

| Measurement | Result |
| --- | ---: |
| Timed workload including last in-flight request | 122.23 s |
| Completed fictional booking sessions | 39 |
| Booking session p95 | 162.7 ms |
| Piper requests | 68 successful, zero busy/errors |
| Vosk requests | 58 successful, zero busy/errors |
| Piper successful request p95, including waiting | 5.49 s |
| Vosk successful request p95, including waiting | 4.77 s |
| Production health | 282 samples, zero errors, p95 16.1 ms |
| Lifetime peak memory, Piper / Vosk / web | 504.62 / 334.21 / 69.96 MiB |

The database contained exactly 39 clients, 39 bookings and 39 pending confirmations, with no other confirmation status or administrator accounts. No delivery worker ran. No memory-limit or OOM events occurred. Production container IDs, start times and healthy states matched before and after; all three staging containers and their network were removed.

Compared with the [one-minute candidate run](speech-queue.md), the observed request latency is similar and all paired speech requests again succeeded. Piper's lifetime peak rose to about 505 MiB in this longer run; this does not establish a memory plateau or leak. Keep the existing 1 GiB Piper / 1.5 GiB Vosk caps and one active inference. The different request totals follow closed-loop pacing and inference time, so they are not maximum throughput measurements.

The test still uses direct speech adapters alongside typed booking requests, rather than concurrent public browser speech sessions. It cannot establish end-to-end browser cancellation, long-term stability, microphone accuracy, or capacity at production's one-CPU speech limits. Do not repeat short synthetic runs as a substitute for those checks. The next human-operated check is the [physical microphone protocol](microphone-evaluation.md); off-host recovery also remains open. No runtime deployment accompanies this validation.
