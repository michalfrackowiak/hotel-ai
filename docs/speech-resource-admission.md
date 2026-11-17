# Speech resource preview

**Planned speech assignments → Check transition budget** now includes a separate resource preview after the configured overlap checks pass. It uses the same inventory snapshot and captured destination plan. Passing configured-budget checks alone does not mean the resource preview passed.

The preview defaults to blocked. Configure `HOTEL_SPEECH_ADMISSION_POLICIES` in the private web environment (passed through Compose) after checking the native Linux deployment topology. It maps host labels to exactly these fields:

| Field | Meaning |
| --- | --- |
| `daemon_id` | Expected daemon ID, matching the controller's pinned local-socket identity |
| `topology_confirmed` | JSON `true`: operator confirmation that the controller measurements describe the intended daemon host and storage filesystem |
| `memory_reserve_mib` | Positive integer memory reserve for the operating system, web service and other demands |
| `disk_reserve_mib` | Positive integer free-space reserve |
| `artifact_requirements_mib` | Map from each approved pinned image reference to a positive integer peak storage estimate in MiB |

Topology confirmation is a manual operational assertion, not machine verification. Do not set it for an unverified remote daemon, Docker Desktop proxy socket, mismatched filesystem or constrained controller environment. The controller's explicit socket and daemon-ID pin are also required. Empty/malformed policy, unverified identity, unsupported or missing observations, and missing artifact estimates block the preview. Policies are local configuration, not editable admin records; error messages do not expose their raw contents.

## Conservative checks

- Available physical memory must cover **the full configured overlap RAM plus the memory reserve**. Active workers are deliberately reserved again because their current usage and potential growth are not measured. This can reject an otherwise viable transition, but avoids assuming active workers will remain small.
- Reported logical CPU count must cover configured overlap CPU, in addition to the existing reserved-budget check. Logical CPU count is not measured idle capacity.
- Free space on the configured filesystem must cover the peak artifact estimate for every distinct planned image, plus the disk reserve. Shared images count once. No credit is taken for cached artifacts.

Artifact estimates must include images, models, extraction/staging space and other peak installation needs for that image. If the same image uses different model sets across deployments, its estimate must cover their combined peak requirement. A single filesystem measurement does not cover files installed on other mounts. These are operator estimates, not downloaded-byte or checksum verification.

## Not execution authorization

A successful result is a point-in-time preview only. The controller must revalidate actual topology and resources immediately before execution, serialize switches, protect shared workers, drain requests, verify artifacts/readiness and roll back failures. No capacity is reserved and no containers, routes or database observations are changed by viewing this page.

The separate [controller resource gate](speech-controller-admission.md) uses immutable local policy, approved Docker limits and fresh observations during lifecycle execution. It does not consume or trust the admin preview result.
