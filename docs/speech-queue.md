# Bounded speech waiting

Each Piper and Vosk process runs one inference at a time and allows one additional validated request to wait for up to three seconds. English and Polish share these slots. A third request or an expired wait receives HTTP 429 with `Retry-After: 1`. Validation and input-size limits remain unchanged.

The Django adapter permits at most two outbound calls per engine and three speech calls in total per web process. It adds no waiting queue. The combined limit leaves one of the deployed four Gunicorn threads available for chat and health requests. Revisit this limit when changing worker/thread configuration; runtime limits remain authoritative across web processes.

The browser displays a localized busy message, stops microphone capture/playback and focuses the text field. The complete current question is restored even if only part of it was spoken. Guests can restart voice or type; there is no automatic retry loop. Booking state and revision remain unchanged, and stale speech responses still return 409 before busy/success handling.

While waiting, the runtime checks whether its direct HTTP peer disconnected, then checks again before inference. Disconnected queued requests are removed without inference. Active inference is not preempted. Browser cancellation through synchronous Django/Gunicorn and urllib does **not** guarantee that the upstream socket closes immediately: stale work can finish, but the browser and server revision checks prevent it from changing the conversation. The three-second limit covers waiting only, not total inference time; existing HTTP timeouts still bound the caller's wait.

Real threaded HTTP tests use controlled fake model work to verify cross-language waiting, immediate overflow, timeout removal, disconnected waiters, exception recovery and maximum active concurrency of one. Django tests cover upstream/local busy mapping, slot recovery, combined admission and stale revision precedence. These tests do not measure acoustic quality.

## Operations

There is no database migration or new credential. Release web, Piper and Vosk together after CI and candidate verification. Keep existing one-CPU and memory limits. Rollback can restore the previous images without changing booking data; older runtimes reject simultaneous calls immediately instead of waiting. Existing contact-confirmation state remains supported by both revisions.

## Candidate staging, 16 November 2026

[Raw results](speech-queue-staging-results.json) use the same isolated 60-second mixed workload and half-CPU limits as the [previous baseline](staging-mixed-load.md). Candidate images overlaid this revision's runtime service and web files onto the deployed images, preserving models and dependencies. Image IDs are recorded in the report. The benchmark used the existing runner with only its source-image lookup redirected to those candidate tags; production identity checks still inspected the original running services.

All 34 Piper and 30 Vosk requests succeeded, compared with 27/54 and 24/48 in the earlier immediate-rejection run. The request totals differ because this is a paced closed-loop workload: waiting reduces the number of pairs submitted. Successful request p95, including queue wait, was 5.47 seconds for Piper and 4.76 seconds for Vosk. This is the expected latency tradeoff, not a throughput or accuracy claim. One-minute configured load completed its final request after 63.37 seconds.

Twenty fictional booking sessions completed with matching isolated clients and pending confirmations; session p95 was 208 ms. No notifications were dispatched. Production health had zero errors, container identities/start times/health were unchanged, and all staging containers and the network were removed. This test exercises adapters directly alongside typed booking transactions, so it does not validate browser-to-runtime cancellation or all four web threads under mixed speech load. The separate admission tests enforce the three-call web cap. The controlled HTTP runtime tests cover overflow, timeout and disconnect cases that this successful paired workload did not trigger.
