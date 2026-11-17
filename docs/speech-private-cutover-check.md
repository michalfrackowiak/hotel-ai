# Private admin cutover check

The production listener design uses Gunicorn in the host network namespace, bound to IPv4 loopback. Nginx serves guest paths, and staff reach Django through an SSH local port forward. The controller uses its own authenticated loopback listener. The private controller and all four worker switches were already exercised in the [ARM64 runtime drill](speech-runtime-arm64-staging.md).

This check uses a cached ARM64 web image, reviewed source mounted read-only and a fresh fixture database in container tmpfs. Gunicorn runs as UID/GID `10001:10001` with one worker and four threads. The fixture port substitutes for production port 8080. The workstation sends a temporary fixture password over SSH standard input and performs Django's normal login POST with a CSRF cookie and form token. The same workstation checks that a direct TCP connection to the fixture port fails and the public HTTPS admin URL returns 404.

The existing production container runs as `hotel` with numeric ownership `10001:10001` on the booking directory and database. Keep that ownership for production Django. A shared traffic directory and the controller service identity need matching numeric access, as described in the [rollout guide](speech-production-rollout.md).

The current speech limits remain one CPU each, with 1 GiB for Piper and 1.5 GiB for Vosk. The proposed initial routed limits are 0.4 CPU per speech worker and 0.25 CPU for Django, with the same speech memory caps, a 512 MiB Django limit and 3 GiB memory headroom for the host. A three-worker overlap totals 1.2 CPU and at most 4 GiB of speech allocations. Including Django gives 1.45 CPU against two physical server cores. The admitted short ARM64 drill used these caps. Sustained production load and physical microphone checks remain distinct from this rehearsal.

## Evidence

Verification pending.
