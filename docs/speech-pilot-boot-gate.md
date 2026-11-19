# Boot gate for routed speech

Use `python3 -m speech_controller.runtime --boot-workers --config /absolute/private/controller.json` as the dedicated controller account. Supply the same private control credential through the service environment. This mode holds the route owner lock across the worker checks and controller lifetime.

Boot requires the original route store and journal to agree. It refuses blocked operations, pending recovery, closed admission, retained claims, and unexpected running workers. It also requires capacity for the selected workers and verifies the approved private endpoints. It starts only missing workers selected by the saved routes. The listener opens after all four language and task slots pass bound inference. If an attempted start leaves a worker running, preserve that state for inspection. A retry checks the same store and worker identities again. Ordinary runtime startup remains available for observing uncertain state and arranging explicit recovery.

## Persistent service design

The live pilot uses a transient service today. Install and verify the boot gate in a disposable systemd host before changing the production service.

1. Order the controller after the local Docker service and the filesystem containing the private stores. Use the existing locked service account, a root-managed environment file, the approved release, and Docker access scoped to the process.
2. Wait for authenticated status from the boot-gated listener before starting Django. A process start alone does not establish readiness. Give the status check a bounded wait and require matching route and journal state with no pending operations.
3. Make the controller readiness check a dependency of the booking web service. Disable independent Docker auto-start of Django as part of the same change. On shutdown or controller failure, the web service must stop accepting requests before workers or controller state are changed. Keep the existing loopback listeners and SSH-only admin access.
4. Rehearse a real reboot on a disposable host. Verify that clean persisted state starts only referenced workers and reaches guest health, and that uncertain state leaves the booking writer stopped. Preserve the stores for inspection in the blocked case.

The boot command expects Django and other writers to be offline until readiness. The systemd units passed the disposable reboot rehearsal. Keep the live pilot on the existing manual restart procedure until the staffed installation. [Persistent systemd units and reboot evidence](speech-pilot-systemd.md) · [Production restart boundary](speech-pilot-live.md)

## Verification

The 18 November 2026 ARM64 run finished in 153 seconds using a checksum-verified source archive. The boot command refused the deliberately interrupted operation without editing either store or changing worker states. After all four routes switched, the fixture stopped and booted using the same files. The command restarted only the selected pair, restored authenticated revision-4 status, and Django reopened both fictional booking drafts with bilingual speech and intact audit history.

All 206 production loopback health samples passed, with p95 50.13 ms and maximum 74.11 ms. The production controller process and seven container identities matched. Fixture resources and temporary source were removed. The archive SHA-256 was `e1abc5174e8b2d3ba701418df964fc2a154afaadb22c1313d06446cc07457e95`.

All 18 runtime tests passed on isolated Linux. Independent review verified that removing either the retained-claim check or the repeated file-identity check causes regression tests to fail.
