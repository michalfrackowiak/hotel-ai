# Explicit private controller startup

The native Linux controller can be composed from a private JSON manifest using `python -m speech_controller.runtime --config /absolute/private/controller.json`. Supply the separate random bearer token through `HOTEL_SPEECH_CONTROL_TOKEN`; never put it in the manifest, command-line arguments or source control. `--check` validates configuration and existing state and inspects approved workers without opening a listener or switching workers.

The manifest must be a private, owner-controlled regular file, at most 64 KiB, with no symlink path aliases. The manifest and database files must have no group/other access (typically mode `0600`), belong to the process user, and reside in a private directory (typically `0700`). Hard links are rejected. Unknown fields, duplicate JSON keys and invalid types are rejected. The loader is Linux-only. The runtime requires the native host and bridge topology described in [endpoint binding](speech-endpoint-binding.md), including stable private IPv4 addresses reachable by both the controller and guest dispatch.

## Manifest fields

| Field | Required value |
| --- | --- |
| `version` | Integer `1` |
| `docker_socket`, `daemon_id` | Absolute local Docker socket and pinned daemon identity |
| `actor`, `port` | Service credential audit label and loopback control port |
| `traffic_path`, `journal_path` | Distinct existing private nonempty route and lifecycle databases |
| `policy` | Resource and topology policy described below |
| `deployments` | One to eight complete approved deployment records |

Each deployment has exactly `name`, `container_id`, `image_id`, `nano_cpus`, `memory_bytes`, `owner`, `task`, `languages`, `base_url`, `network_id` and `address`. Container and network IDs are full identities, images use immutable SHA-256 IDs, and URLs are canonical direct bridge HTTP endpoints. Resource limits and ownership must match Docker observations. English/Polish STT/TTS assignments come from the existing route store, not the manifest.

The policy has `storage_path`, `topology_confirmed`, `cpu_budget_nanos`, `memory_budget_bytes`, `memory_reserve_bytes`, `disk_reserve_bytes` and `artifact_bytes`. The latter is a list of distinct `[image_id, estimated_bytes]` pairs covering all approved images. Budgets and reserves are positive integers. `topology_confirmed` must explicitly be `true`; it records the operator's topology assertion rather than automatically proving co-location.

## Existing state and ownership

Prepare the route store and durable routed journal offline using their explicit initialization APIs before starting the service. Both stores must retain their supported SQLite DELETE journal mode; startup rejects WAL rather than converting it. This command does not initialize, replace or clear missing, empty or incompatible databases. Preserve both files together and retain their physical identities and immutable catalogs. Startup leaves pending operations and disagreement between journal and observed routes available for explicit recovery rather than resetting them.

A persistent sidecar at `<traffic_path>.controller.lock` supplies a lifetime exclusive lock. A second runtime targeting the same canonical route store is rejected even if it names another journal. The existing journal operation lock remains separate. Never delete a live lock file or run older manually constructed controllers alongside the runtime. The directory and its files must remain under trusted local control throughout execution.

Shutdown waits for active HTTP handlers before releasing ownership. Use the canonical Docker socket path, such as `/run/docker.sock` where `/var/run` is a symlink; aliased socket paths are rejected too.

Startup composes the same pinned controls, resource admission, bound readiness, traffic gate and durable executor used by switching. It does not start/stop workers, reopen traffic, invoke speech inference or reconcile pending operations. The listener remains restricted to `127.0.0.1`; the inventory service remains a separate read-only command.

## Recovery capacity

Recovery uses a concrete verifier that re-inspects every approved worker after the backend's endpoint/readiness checks. Selected workers must be running, unselected workers must be stopped, and observations must still match. The same conservative CPU, RAM and disk capacity calculations used for switch admission must pass before traffic can resume. These checks do not reserve resources, so later host changes can still invalidate available capacity.

## Validation and rollout

Tests exercise strict configuration, existing-store validation, owner locking, state preservation and failure handling with fictional Docker observations. Existing CI fixtures independently exercise real Docker endpoint checks, HTTP switching/recovery and bilingual Piper/Vosk probes. An end-to-end deployment of this startup command with real configured model workers is still required before production activation.

This milestone installs no service and changes no production routes. Next work is private admin status/switch/recovery integration with staff authorization, CSRF protection, durable request IDs and clear handling of blocked or uncertain results, followed by a controlled staging deployment of the complete runtime.
