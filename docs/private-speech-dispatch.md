# Private speech operation dispatch

`speech_controller.dispatch.ControlServer` exposes the durable routed controller through a separate authenticated loopback listener. It must be constructed with a `ControlConfiguration` and an already configured `RoutedLifecycleExecutor`. There is no environment-driven startup command, Compose activation or public proxy route for this service. The inventory command remains read-only.

The server binds only to `127.0.0.1` (default port 8093). Use a distinct random bearer token kept in private configuration; the token is omitted from the configuration representation. A configured actor label identifies the service credential in recovery audits. It does not establish the identity of an individual Django administrator. Request bodies cannot supply or override that actor.

## Protocol

All requests require `Authorization: Bearer <token>`. Mutation requests require `Content-Type: application/json`, one valid `Content-Length`, and a JSON object no larger than 4096 bytes. Duplicate JSON keys, extra fields, ambiguous framing and transfer encoding are rejected. Revisions are nonnegative integers, not booleans, bounded to SQLite's signed integer range.

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /v1/status` | No body | Journal `state`, actual route `observed`, `blocked`, `pending_requests`, `pending_recoveries` |
| `POST /v1/switch` | `request_id`, `expected_revision`, `slot: [language, task]`, `target` | Durable switch result |
| `POST /v1/recover` | `recovery_id`, `expected_revision`, `observed`, `reason` | Durable recovery result |

A state has `revision` and `routes`, where routes contain four distinct `[[language, task], deployment_name]` entries for English/Polish STT/TTS. Recovery routes must reference compatible approved deployments. Status reads the journal and route store separately; it is an observation, not a reservation. Switch and recovery requests revalidate revisions and actual state before acting.

HTTP 200 means the controller processed a valid request. Clients must inspect the result's `status` and `reason`: a rejected switch or a recovery-required result is not a successful provider change. Authentication failures return 401, malformed requests 400, unknown paths 404, wrong methods 405 (unsupported HTTP verbs 501), dispatch contention 429 with `Retry-After`, and unavailable controller/storage state 503. Errors do not expose exception details or request bodies. Responses are not cached, and request logging is disabled.

The server admits at most eight connection workers and one dispatched operation at a time. Body reads have a total three-second deadline; lifecycle effects retain their own existing bounded checks. Losing the HTTP connection does not cancel an operation already dispatched. After a lost response, retry the same operation ID with the same payload; do not generate a fresh ID or assume the operation failed. A recovery retry must preserve its original expected revision and observation even if the journal has advanced to its checkpoint.

## Ownership and deployment boundary

The injected executor owns its verified deployment catalog, pinned Docker controls, admission policy, traffic store, readiness probes and reconciliation verifier. None of those settings can be selected or changed through HTTP. One controller owner must serialize operations for a route store. The API does not install containers, delete uncertain speech claims, repair worker state or bypass recovery verification.

Tests use real local HTTP connections and SQLite stores with fictional workers. CI also dispatches a switch and recovery through HTTP against the existing constrained Docker fixture, checks unauthorized requests leave it unchanged, and verifies replay, observed state and the configured recovery actor. Its readiness and external verifier remain fictional; separate CI probes exercise real bilingual Piper/Vosk inference.

Production startup still requires validated local composition and endpoint-to-container binding. Private Django admin integration must supply authorization and CSRF protection and distinguish completed results from blocked/recovery states. This milestone does not deploy or enable provider switching on the public hotel website.
