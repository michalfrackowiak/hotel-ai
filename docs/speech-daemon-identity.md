# Speech controller daemon identity

Inventory includes the Docker daemon ID, name, operating-system type and connection mode. This makes the source of container observations visible before host-resource admission is implemented.

For a Linux controller, configure both `HOTEL_SPEECH_CONTROLLER_DOCKER_SOCKET` and `HOTEL_SPEECH_CONTROLLER_EXPECTED_DAEMON_ID` in its private environment. The socket must be an absolute path to a Unix socket; the expected daemon ID must be obtained and checked by the operator on the intended daemon. Both values are required together. Do not put the socket into the public web container.

With these values set, the controller explicitly selects that socket for Docker calls, ignores Docker host/context/TLS environment overrides and rejects a daemon whose reported ID differs. The report labels this connection as pinned to a local socket. Paths and Docker errors are not included in responses. Without this configuration, existing Docker-context behavior remains available for observation, clearly labeled unverified.

An explicit local socket and matching daemon ID prevent environment-based context changes; they do **not** prove physical co-location. A Unix socket may proxy another machine or a Docker Desktop VM, and a daemon ID is a configuration pin, not cryptographic attestation. Identity lookup and container inspection are separate calls, so the observation does not exclude a daemon restart or socket replacement between them. Operators must still establish that controller memory/filesystem observations describe the intended native Linux daemon host. No resource admission or activation is enabled by the connection label.

Older controller responses without identity data remain readable with identity unknown. New responses are validated before display. The controller uses a shared subprocess time budget for the identity and container queries, and failures produce an unavailable snapshot rather than partial identity/container results.

Next work must combine verified deployment topology, explicit resource reserves and artifact requirements with serialized execution, draining, readiness and rollback. Neither identity pinning nor a transient observation is a durable activation permit.
