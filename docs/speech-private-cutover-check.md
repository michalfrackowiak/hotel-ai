# Private admin cutover check

The production listener design uses Gunicorn in the host network namespace, bound to IPv4 loopback. Nginx serves guest paths, and staff reach Django through an SSH local port forward. The controller uses its own authenticated loopback listener. The private controller and all four worker switches were already exercised in the [ARM64 runtime drill](speech-runtime-arm64-staging.md).

This check uses a cached ARM64 web image, reviewed source mounted read-only and a fresh fixture database in container tmpfs. Gunicorn runs as UID/GID `10001:10001` with one worker and four threads. The fixture port substitutes for production port 8080. The workstation sends a temporary fixture password over SSH standard input and performs Django's normal login POST with a CSRF cookie and form token. The same workstation checks that a direct TCP connection to the fixture port fails and the public HTTPS admin URL returns 404.

The existing production container runs as `hotel` with numeric ownership `10001:10001` on the booking directory and database. Keep that ownership for production Django. A shared traffic directory and the controller service identity need matching numeric access, as described in the [rollout guide](speech-production-rollout.md).

The current speech limits remain one CPU each, with 1 GiB for Piper and 1.5 GiB for Vosk. The proposed initial routed limits are 0.4 CPU per speech worker and 0.25 CPU for Django, with the same speech memory caps, a 512 MiB Django limit and 3 GiB memory headroom for the host. A three-worker overlap totals 1.2 CPU and at most 4 GiB of speech allocations. Including Django gives 1.45 CPU against the server's two logical CPUs. The short ARM64 drill used these caps. Sustained production load and physical microphone checks remain distinct from this rehearsal.

## Evidence

The 18 November 2026 rehearsal used candidate commit `5b5457c1f81c2eac25a991bf0cbf1e2c0825fec9`. The source archive matched SHA-256 `9f66a50545da3cfbc039d25a598a5ede07276db95029df18e6e94e9e05ba7384` after transfer.

- The server observed only `127.0.0.1:28089` for the fixture listener. The workstation authenticated through the SSH forward, including the CSRF check, and reached Django admin.
- The workstation could not open a direct TCP connection to the fixture port. Public HTTPS `/admin/` returned 404.
- The server passed repeated production and fixture health checks for the rehearsal window. Production container IDs, images, start times and health matched before and after. A follow-up check confirmed the temporary listener, container and source archive directory were removed.

Two pre-start attempts exposed Windows newline conversion in the archived shell script and in the SSH credential pipe. The successful run used a file-specific Git LF rule and ASCII bytes with a single LF on stdin. Both earlier attempts stopped before container creation and passed cleanup checks.

The controller connection and proposed limits were exercised separately in ARM64 staging. Production activation still needs the final owner of the shared traffic directory, approved worker IDs and a cutover window. [ARM64 staging results](speech-runtime-arm64-staging.md)
