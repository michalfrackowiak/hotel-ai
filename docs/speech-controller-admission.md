# Controller resource admission

The lifecycle resource gate evaluates fresh local host observations against immutable controller policy and the limits of approved Docker containers. It runs before draining and again after draining, before starting a replacement. A denial before draining leaves traffic untouched; a denial after draining keeps the previous routes and attempts to verify and resume the old worker without starting the replacement.

The backend inspects every approved container, including workers outside the current and proposed routes. Docker controls verify pinned daemon identity, container/image identity, ownership and configured limits. Admission uses the same controls' approvals rather than editable admin estimates. The existing Django transition page remains a preview and cannot authorize a switch.

## Private policy

Construct `ResourcePolicy` in trusted controller configuration and bind `ControllerAdmission(controls, policy)` to the same `DockerControls` instance used by the lifecycle backend. The backend rejects a gate bound to another controls instance. The gate re-inspects all approved containers and rejects observations that changed before reading host resources.

| Field | Required value |
| --- | --- |
| `daemon_id` | The controls' pinned Linux daemon ID |
| `storage_path` | Absolute path on the verified host/model filesystem |
| `topology_confirmed` | Exactly `True`, after operator verification described below |
| `cpu_budget_nanos` | Positive speech CPU budget in billionths of one CPU |
| `memory_budget_bytes` | Positive maximum full-overlap speech RAM allocation |
| `memory_reserve_bytes` | Positive available-RAM reserve beyond full overlap |
| `disk_reserve_bytes` | Positive free-storage reserve |
| `artifact_bytes` | Immutable tuple of unique `(sha256 image ID, positive peak bytes)` pairs covering every approved image |

There is no permissive policy default. Budgets are integer values; booleans and invalid image identities are rejected. Policies and container approvals remain immutable for the gate's lifetime. Changes require constructing a new verified controller configuration.

## Conservative accounting

- Count each container in the current routes, proposed routes or observed running set once. Retain the old worker's full limits even if the switch would later stop it.
- Require overlap CPU to fit both the configured speech budget and observed logical CPU count. Logical CPUs do not measure idle CPU capacity.
- Require overlap RAM to fit the configured speech budget, and available memory to cover the full overlap plus the local reserve. Already-running workers receive no credit for memory they currently use.
- Require free space on the approved filesystem to cover the disk reserve plus peak artifact estimates for each distinct overlap image. Shared images count once; cached artifacts receive no credit. An image estimate must include the combined peak requirements of its model sets.

Missing, malformed or unsupported observations deny admission. The gate obtains new host measurements on each evaluation and does not reuse the admin inventory snapshot. Failed inspection or capacity checks cannot cause a container start.

## Operational boundary

The operator must explicitly confirm that native Linux controller observations and the configured filesystem describe the intended Docker host and model storage. This is a local operational assertion, not automatic topology attestation. Do not enable it for an unverified remote daemon, Docker Desktop proxy socket, mismatched storage mount or constrained controller environment.

The checks do not reserve capacity or make Docker inspection and host measurement atomic. Other workloads can change resources immediately afterward. Switch serialization, approved limits and conservative reserves reduce that risk; production still needs verified host placement, guest admission/draining, atomic routing and authenticated dispatch. Local filesystem observations also depend on operating-system responsiveness.

CI verifies deterministic denial and successful shared-worker switching using constrained disposable containers and real host observations. Its small resource policy includes synthetic artifact estimates for the already-built test image; these are not production image/model sizing evidence. Its traffic gate and readiness callback remain fictional: the fixture does not establish production capacity, guest draining or model quality. No production deployment or admin switch action is enabled by this change.
