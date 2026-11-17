# Approved speech endpoint binding

`EndpointBinding(name, network_id, address)` associates an approved speech deployment with a full Docker network ID and a literal private IPv4 address. `BoundReadiness(controls, targets, bindings)` checks this association before and after a real speech readiness probe. It uses the same pinned `DockerControls` instance as the lifecycle backend.

## Supported topology

This binding supports a native Linux Docker host and direct IPv4 access to containers on a local bridge network. The controller must run in the host network namespace; guest dispatch must have access to the same approved bridge addresses. Targets use canonical `http://<private-ip>:<port>` URLs with explicit ports. DNS names, public addresses, loopback addresses, link-local addresses, redirects and host/container network sharing are outside this binding's supported topology.

Docker documents that containers on bridge networks are reachable from the Docker host and other containers on that network. No published host port is required for this path. See [Docker networking](https://docs.docker.com/engine/network/) and [host networking limitations](https://docs.docker.com/engine/network/drivers/host/).

The address must come from RFC 1918 space and match the approved container's current address on the exact network ID. The network must use the local bridge driver. Configure stable container addresses before production use; replacing a container or recreating a network changes its identity and requires a controlled update of approvals and the immutable route catalog. This check does not choose addresses, create networks or update routes.

## Checks and failure behavior

`DockerControls.verify_endpoint` reuses pinned daemon and approved container validation: full container ID, name, image ID, owner, resource limits and nonprivileged state. It additionally requires a running container, the exact bridge network identity and address, and a supported network mode. Docker observations use fixed arguments, bounded execution and projected metadata; raw inspect output is not forwarded to clients.

Bound readiness requires matching deployment/target/binding catalogs and compatible language/task requests. A binding mismatch prevents the probe from running. A changed binding after a successful probe rejects readiness. Backend composition rejects concrete bound readiness using a different control instance or mismatching catalogs. Unknown or unavailable Docker state fails closed.

These checks are observations around the probe, not an atomic network reservation or cryptographic service identity. They establish the Docker endpoint association under the trusted local daemon and host policy. They do not attest model files or accuracy, prevent a privileged host administrator from changing networking, or guarantee the endpoint remains unchanged after verification. Existing lifecycle admission, durable routing, traffic draining and recovery checks still apply.

Each Docker verification shares a ten-second command budget. A bound readiness call combines two such checks and the existing eight-second probe budget, up to 28 seconds plus process scheduling overhead; it does not retain the probe-only eight-second total. Multi-slot recovery runs these checks sequentially.

## Verification and activation

Tests cover malformed approvals, wrong container/network/address, unsupported topology, catalog disagreement, failure before probing and drift after probing. CI creates one constrained sleeping-process container on a disposable internal bridge, publishes no ports, verifies its address and rejects wrong/stopped endpoints, then removes the container and network. This fixture checks endpoint identity only; separate Piper/Vosk checks validate speech inference.

The adapter is opt-in Python composition. No production service, network, route or admin selection changes automatically. Next work is a validated startup configuration that composes this adapter with the real readiness probes, resource policy, route store, durable executor and private control API, followed by private admin switching and recovery.
