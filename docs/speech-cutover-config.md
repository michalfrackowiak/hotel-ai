# Routed speech cutover configuration

`compose.speech-routed.yaml` is an opt-in overlay for `compose.yaml` and `compose.server.yaml`, in that order. It places Gunicorn in the host network namespace on `127.0.0.1:8080`. The overlay clears the inherited Docker port mapping with `!reset` and keeps the booking volume at `/app/data`. In the existing `hotel-ai` Compose project this is the existing `hotel-ai_hotel-data` volume. The healthcheck uses the same loopback port. [Docker Compose merge rules](https://docs.docker.com/reference/compose-file/merge/)

The pilot has staff switch and recovery actions disabled. Django uses the controller's loopback endpoint at `127.0.0.1:8093` for private status. The SSH admin path and public Nginx allowlist stay as described in the [rollout guide](speech-production-rollout.md).

## Private inputs

| Setting | Required value |
| --- | --- |
| `HOTEL_ROUTED_PIPER_IMAGE`, `HOTEL_ROUTED_VOSK_IMAGE` | Exact approved image IDs already available on the ARM64 host |
| `HOTEL_ROUTED_SPEECH_SUBNET` | Private IPv4 subnet absent from the host and Docker networks |
| `HOTEL_ROUTED_PIPER_PRIMARY_IP`, `HOTEL_ROUTED_PIPER_STANDBY_IP`, `HOTEL_ROUTED_VOSK_PRIMARY_IP`, `HOTEL_ROUTED_VOSK_STANDBY_IP` | Four distinct addresses on that subnet, outside the gateway and reserved addresses |
| `HOTEL_ROUTED_TRAFFIC_DIR` | Absolute pre-existing private host directory with the shared `traffic.sqlite3` |
| `HOTEL_SPEECH_ROUTE_TARGETS` | The same approved four endpoints as the controller, using literal bridge addresses and the full container names below |
| `HOTEL_SPEECH_CONTROL_TOKEN`, `HOTEL_SPEECH_CONTROL_HOST` | Private controller token and matching planning host label |

Keep the existing Django secret key and public host settings. Store the controller credential with private runtime configuration. Use quiet Compose validation when supplying real credentials because a full render includes environment values.

| Service | Container name | Profile |
| --- | --- | --- |
| `routed-piper-primary` | `hotel-ai-routed-piper-primary` | `routed-primary` |
| `routed-vosk-primary` | `hotel-ai-routed-vosk-primary` | `routed-primary` |
| `routed-piper-standby` | `hotel-ai-routed-piper-standby` | `routed-standby` |
| `routed-vosk-standby` | `hotel-ai-routed-vosk-standby` | `routed-standby` |

Run these commands from the production checkout with the reviewed private environment loaded. Use both profiles with `config -q` to validate all four specs without displaying credentials.

```sh
docker compose --project-name hotel-ai -f compose.yaml -f compose.server.yaml \
  -f compose.speech-routed.yaml --profile routed-primary --profile routed-standby config -q
docker compose --project-name hotel-ai -f compose.yaml -f compose.server.yaml \
  -f compose.speech-routed.yaml create --no-build --pull never --no-recreate \
  routed-piper-primary routed-vosk-primary routed-piper-standby routed-vosk-standby
```

Inspect the resulting worker specs and record their full container and network IDs for the controller approvals. Explicit service names limit creation to the workers. Prepare the private route store and journal while all four workers are stopped. Keep the existing fixed workers available for rollback. At the staffed cutover, quiesce traffic and stop the old fixed workers before starting the two primaries. [Offline initialization procedure](speech-pilot-initialization.md)

```sh
docker compose --project-name hotel-ai -f compose.yaml -f compose.server.yaml \
  -f compose.speech-routed.yaml start routed-piper-primary routed-vosk-primary
```

Leave the standby pair stopped for controller admission. Omit `--remove-orphans` to preserve the fixed workers. The routed workers have no automatic restart policy. After a host restart, verify the journal and actual routes before starting the referenced workers and reopening routed traffic.

## Ownership and headroom

Django runs as UID/GID `10001:10001` and mounts the existing booking volume and the traffic directory. Give the native controller the same numeric UID and a private host-only directory for its journal, manifest and credential. Reserve UID/GID 10001 for this purpose during provisioning. The service account needs deliberate access to the host Docker daemon, which is privileged. Django needs access to the traffic directory and database file alone for controller coordination. Initialize them as the runtime owner with modes `0700` and `0600`, respectively, and verify that both processes see the same route-store inode. Keep the booking volume's existing ownership.

The overlay carries forward the short ARM64 rehearsal limits. With one overlap, the worker budget totals 1.2 CPU and at most 4 GiB RAM. Django adds 0.25 CPU and 512 MiB. Reserve 3 GiB of host memory during admission and confirm current disk headroom with real image sizes. The controller's private policy owns these admission decisions.

The read-only host preflight found Compose 5.6.1, healthy production web, and UID/GID 10001 unassigned in the host account database. The host routes used `10.0.0.0/24` through `10.0.4.0/24` at inspection time. Recheck route conflicts and listener availability immediately before provisioning.

## Validation

Run `python3 scripts/check_speech_routed_compose.py` for the config checks with fictional values. It renders the overlay without creating Docker resources. It checks the effective listener, mounts, action flags, optional worker profiles, pinned image inputs, and bridge settings. Hosted CI runs the same checks.

The configuration passed on Windows with Docker Compose 5.7.0 and on the ARM64 server with Compose 5.6.1. The server verified the fixture archive checksum, rendered with fictional values, confirmed production web health, and removed the temporary copy.
