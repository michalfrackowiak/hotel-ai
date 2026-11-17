# Live speech controller status in admin

Open **Speech selections → Controller status** in Django admin to inspect the private controller's current journal and observed route state. The view requires all four speech catalog view permissions (selection, deployment, provider and model), in addition to an active staff login. It accepts GET requests only and never changes selections, worker state or controller operations.

Configure these settings in private Django configuration:

| Setting | Value |
| --- | --- |
| `HOTEL_SPEECH_CONTROL_URL` | Canonical `http://127.0.0.1:<port>` for the separate control API |
| `HOTEL_SPEECH_CONTROL_TOKEN` | Matching private control-service bearer token |

Both default to empty; the page reports that the controller is not configured until enabled. These settings are separate from the read-only Docker inventory service's `HOTEL_SPEECH_CONTROLLER_*` settings. Do not reuse a public application endpoint or expose the control service through the public reverse proxy.

The loopback address must be reachable from the Django process's network namespace. For a controller on another host, use a private SSH tunnel whose listener is in that namespace. A Django container's `127.0.0.1` refers to the container itself. This milestone does not create the tunnel, publish the controller or change production networking.

## Reading the view

The table shows four language/task slots: English STT, English TTS, Polish STT and Polish TTS. Each row compares the journal's selected deployment with the actual route-store selection. Journal and observed route revisions are shown separately. Any revision or route mismatch is marked as requiring attention, even if the controller's blocked flag is false.

Blocked state, unfinished switch IDs and unfinished recovery IDs are displayed explicitly. They do not trigger automatic retries or recovery. Matching routes and an unblocked journal do not prove speech-worker health or available capacity. The page shows when Django fetched the response; the controller reads two separate stores, so the result is an observation rather than an atomic reservation.

Busy, unavailable, invalid and unconfigured states are shown without displaying raw errors or credentials. The page is not cached and refreshes only on a new request. Authorized staff can open the separately enabled [switching workflow](admin-speech-switch.md) or [recovery workflow](admin-speech-recovery.md).

## Transport boundary

The client sends only authenticated `GET /v1/status`, disables environment proxies and redirects, and makes no automatic retries. An isolated helper process enforces a six-second request budget (plus operating-system process startup and reaping overhead), with bounded response bytes and strict JSON/schema validation. The token travels to that helper through standard input, not its command line. Malformed or inconsistent snapshots are rejected rather than partially rendered.

Tests cover real HTTP authentication/path behavior, invalid responses, unavailable/busy states, elapsed deadlines, permission enforcement and read-only rendering. Production activation remains pending. Next: staff-authorized switching and recovery with durable request IDs and clear handling of uncertain results, followed by full runtime staging.
