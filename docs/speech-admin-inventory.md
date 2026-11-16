# Observed speech inventory in admin

Open **Planned speech assignments → Observe speech containers** in the private admin (`/admin/model_providers/speechselection/inventory/`) to fetch a fresh, read-only snapshot from the standalone [speech controller](speech-controller.md). The snapshot compares locally planned deployment records with observed containers; it does not activate a plan or update saved deployment observation fields.

## Configuration

Set these values in the web process's private environment (Compose passes them from `.env`):

| Variable | Meaning |
| --- | --- |
| `HOTEL_SPEECH_CONTROLLER_URL` | Controller base URL, without credentials, query or fragment |
| `HOTEL_SPEECH_CONTROLLER_TOKEN` | Same secret bearer token configured on the controller |
| `HOTEL_SPEECH_CONTROLLER_HOST` | Exact expected host label in the controller response |

The default is disabled. Plain HTTP is permitted only for literal loopback addresses; remote connections require HTTPS with certificate verification. Redirects and environment proxy settings are disabled, so the bearer token cannot be forwarded through a redirect or an implicitly configured proxy. The client requests only `/v1/inventory`, with a six-second socket inactivity timeout and a 64 KiB response limit. This is not an absolute wall-clock deadline for a server that continually sends data. Error messages exclude tokens and raw remote responses.

The controller itself still binds only to loopback. For a same-host development process, use its loopback URL. For a containerized web process, `127.0.0.1` refers to that container, not the Docker host: a deliberately configured private tunnel in the web network namespace or a private TLS gateway to the controller is required. This increment installs neither. Never publish the controller through the hotel's public proxy or mount the Docker socket into Django.

## Reading the snapshot

Staff need view permissions for speech assignments, deployments, providers and models/voices. The snapshot page accepts GET only and is not cached. Invalid responses, wrong host labels, timestamps older than 30 seconds or more than five seconds in the future, and unreachable controllers produce an unavailable report rather than a successful comparison. Keep the web and controller clocks synchronized.

Matching uses the exact host label and container name. Containers without a planning record are identified separately. Planned containers absent from the response are unobserved; this does not prove they were deleted, since the controller inspects only its allowlist. The configured image reference is compared with the catalog reference. Docker's resolved image ID is shown separately and is not treated as the repository manifest digest or model checksum.

CPU and memory comparisons use configured limits, not live usage or host capacity. CPU reflects `NanoCpus` only; an unset value does not exclude other CPU constraints. Container health is descriptive, not a model-readiness test. The timestamp is evidence of when the controller collected the snapshot, not a lasting guarantee. No data is saved to the planning models and no Docker lifecycle action is available.

The next lifecycle increment must validate immutable execution inputs, observe transition resource headroom, serialize changes, drain speech requests, retain shared workers, check model readiness and roll back failures before enabling live routing.
