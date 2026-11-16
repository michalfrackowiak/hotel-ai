# Approved Docker start/stop controls

The controller package includes an internal start/stop adapter for pre-existing, explicitly approved containers. It is not wired to the lifecycle executor, HTTP service, admin actions or guest routing. No production deployment is included.

An immutable approval identifies the exact full container ID, name, resolved image ID, CPU and memory limits, and expected `hotel-ai.controller-owner` label. The adapter requires a native Linux Unix socket and expected daemon ID. It explicitly selects that endpoint and removes Docker context/host/TLS environment overrides.

Before acting, it checks the daemon and projects only the container fields needed for validation. The command targets the full approved ID rather than resolving a name again. Any identity, image, resource-limit or ownership mismatch blocks the operation. Only ordinary stopped-to-running and running-to-stopped transitions are supported; transitional or abnormal states require reconciliation. Repeated requests for an already achieved state do not issue another mutation.

After the command, a fresh inspection must confirm state and approval fields. A timeout or failed post-check is an uncertain outcome, not proof that Docker did nothing. The eventual lifecycle integration must retain its pending journal record and reconcile actual state before retrying. Daemon validation, container inspection and mutation are separate calls, not an atomic Docker transaction; endpoint pinning is not cryptographic attestation.

## Boundaries

This layer cannot pull images, create or remove containers, execute arbitrary commands, or select targets outside its approval map. Approvals are local operator inputs, not automatically populated from editable admin metadata. Resolved image IDs are distinct from manifest digests and model checksums.

The caller must perform authoritative resource admission, reconcile ownership and drain guest requests before mutation. Docker running/stopped state is not model readiness. Starting an approved container can still fail after partial effects, so this adapter must only be connected through the durable lifecycle machinery after integration tests.

The next step is to compose these controls with bounded model-readiness probes, guest admission/draining, durable execution and an authoritative reconciliation verifier. Until then only isolated test fixtures are eligible for execution in the project's verification workflow.
