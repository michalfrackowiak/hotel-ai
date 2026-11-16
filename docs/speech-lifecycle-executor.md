# Speech lifecycle executor core

The controller package includes a switch executor tested through an injected backend. It is not connected to the HTTP inventory service, Docker commands, Django routes or guest sessions. The deployed system still cannot switch providers through admin.

The executor begins with four explicit English/Polish STT/TTS routes and approved deployment capabilities. A switch identifies one slot, an approved target, the expected route revision and an idempotency key. Unknown/incompatible targets, stale revisions and reused keys with different requests are rejected before backend work.

## Switch sequence

The executor serializes operations within one process. The backend revalidates the transition, then drains the affected slot. A replacement is started only if it is not already referenced by another slot. Readiness must pass before the route revision changes. The old deployment is stopped only when no route references it. Shared language workers therefore remain running.

Failures before route commit retain the previous routes and attempt cleanup of a newly started replacement. Backend errors are represented by safe outcome labels rather than raw exception text. Failure to establish recovery or resume service blocks subsequent switches for operator recovery. Failure to clean up an old deployment after committing the ready replacement must not be presented as a fully successful operation.

Retries with the same idempotency key and request return the recorded outcome. A bounded in-memory journal refuses new requests when full rather than forgetting keys and risking replay. A no-op targeting the already selected deployment does not restart services or advance the revision.

## Integration requirements

The injected backend must enforce deadlines for each operation and perform authoritative admission immediately before execution. Admission must reconcile observed ownership: an unreferenced target is not necessarily stopped or safe to take over. The backend must reject an unmanaged target or establish ownership before the core can start and potentially clean it up. Readiness checks receive the affected language/task slot and must verify the required model, including when reusing an existing shared worker. This synchronous core does not interrupt hung backend calls. Its lock and journal are process-local; they do not provide restart recovery, persistent auditing or coordination across processes. Routes in this core are not guest-facing routes.

Before enabling a production endpoint, implement and verify a durable operation journal and reconciliation, a pinned/allowlisted Docker backend, model-readiness probes, actual guest admission/draining and atomic routing integration. Keep booking drafts independent of provider switches. Recovery must account for partial Docker actions, shared ownership and process restarts. No live activation is authorized by a successful unit-test run or admin resource preview.
