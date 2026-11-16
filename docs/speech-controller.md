# Private speech controller: inventory

The standalone controller provides an authenticated, read-only snapshot of explicitly configured Docker containers. It cannot start, stop, pull, remove or switch containers. The [optional admin integration](speech-admin-inventory.md) retrieves these observations when explicitly configured; the private admin's static preflight remains separate.

Run it on the Docker host using Python and the Docker CLI, under a dedicated operating-system account with the necessary Docker access. Docker access itself is privileged: this endpoint's restrictions are not a replacement for isolating that account. Never mount the Docker socket into the public Django service or publish this endpoint through the public reverse proxy.

The listener binds only to `127.0.0.1`. Access from another machine must use a private SSH tunnel. Bearer authentication remains required through the tunnel. Keep the token in private local configuration, not command-line arguments, Git, screenshots or logs. The controller does not log request headers or Docker stderr.

## Configuration

| Environment variable | Value |
| --- | --- |
| `HOTEL_SPEECH_CONTROLLER_TOKEN` | At least 32 ASCII characters without whitespace; generate a random secret |
| `HOTEL_SPEECH_CONTROLLER_HOST` | Host label matching the intended deployment host |
| `HOTEL_SPEECH_CONTROLLER_CONTAINERS` | JSON list of explicitly allowed container names; no client-supplied names |
| `HOTEL_SPEECH_CONTROLLER_PORT` | Integer port from 1 to 65535; defaults to `8092` |
| `HOTEL_SPEECH_CONTROLLER_STORAGE_PATH` | Optional absolute path on the speech-storage filesystem; empty means disk observations are unknown |

Start the service with `python -m speech_controller.service` from the repository root, after configuring the environment. The controller is not enabled in Compose or installed as a production service by this change.

An authorized `GET /v1/inventory` returns `observed_at` (UTC), `host` and a `deployments` list. Each deployment exposes only identity, configured image, resolved image ID, state/health and configured CPU/RAM limits. It excludes environment variables, mounts, labels, health logs and command arguments. Missing containers, Docker failures or malformed responses cause an unavailable response rather than a partial success. Responses are not cached. Requests cannot choose Docker commands or change the allowlist. At most eight connections are handled concurrently, with a three-second socket timeout; one Docker inventory call runs at a time with a five-second deadline. A busy inventory returns HTTP 429.

## Meaning of the observations

Optional [controller-host resource observations](speech-host-resources.md) accompany the inventory. They describe the controller machine, not necessarily the Docker daemon host, and do not reserve capacity.

Configured limits are not current utilization or available host capacity. `cpu_limit` reflects Docker's `NanoCpus` setting only; `null` means it was unset, not that all CPU constraints are absent (quota/period or CPU sets may still apply). `memory_mib: null` means no container memory limit was reported, not that host memory is available. A missing health check is not evidence that a speech model is ready. An image ID is not proof that the intended model files or licensing metadata are correct. The snapshot may become stale immediately after collection.

Future increments must connect authenticated inventory to transition admission, check disk and measured resource headroom, verify pinned artifacts and model readiness, serialize switches, drain in-flight requests, retain shared deployments and roll back failures. Live activation and Django routing remain unavailable until those paths are implemented and tested. No production deployment is included in this increment.
