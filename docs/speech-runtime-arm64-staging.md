# ARM64 speech runtime staging

This drill runs the private speech controller and Django speech workflows on the two-core ARM64 hotel server. It uses the same controller startup, recovery and four-slot switching checks as [hosted runtime staging](speech-runtime-staging.md).

The server mode reads the image IDs from the three healthy hotel services and starts disposable containers from those cached images. Django runs the candidate checkout from a read-only mount with a fresh private SQLite database. The worker network, controller journal and route store belong to the drill. The Django test client reaches the controller through the server's loopback namespace and exercises staff permissions and CSRF with a fixture login. Worker endpoints use the isolated bridge. The staged web container uses the operator's unprivileged UID and a restricted writable fixture directory.

The preflight budgets 4 GiB for speech overlap, 512 MiB for Django and 3 GiB of available memory for the rest of the host. Worker CPU caps are 0.4 cores each and Django is capped at 0.25 cores. With one replacement overlapping the two active workers, the combined cap is 1.45 of the two cores. The health monitor samples the running hotel's loopback health URL and can interrupt the drill. Production container IDs, image IDs, start times and health are compared before and after. Cleanup verifies removal of the current run's Docker resources.

Use the explicit server mode from a reviewed, extracted source archive in the operator's private temporary directory. It requires an existing noninteractive `sudo docker` grant and the native ARM64 daemon. The fixture reuses images and exercises cached-image cold worker starts, not download or build latency.

```sh
python3 scripts/smoke_speech_runtime.py --arm64-server --source "$PWD"
```

## Results

Server verification is in progress.
