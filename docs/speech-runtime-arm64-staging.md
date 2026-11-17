# ARM64 speech runtime staging

This drill runs the private speech controller and Django speech workflows on the two-core ARM64 hotel server. It uses the same controller startup, recovery and four-slot switching checks as [hosted runtime staging](speech-runtime-staging.md).

The server mode reads the image IDs from the three healthy hotel services and starts disposable containers from those cached images. Django runs the candidate checkout from a read-only mount with a fresh private SQLite database. The worker network, controller journal and route store belong to the drill. The Django test client reaches the controller through the server's loopback namespace and exercises staff permissions and CSRF with a fixture login. Worker endpoints use the isolated bridge. The staged web container uses the operator's unprivileged UID and a restricted writable fixture directory.

The preflight budgets 4 GiB for speech overlap, 512 MiB for Django and 3 GiB of available memory for the rest of the host. Worker CPU caps are 0.4 cores each and Django is capped at 0.25 cores. With one replacement overlapping the two active workers, the combined cap is 1.45 of the two cores. The health monitor samples the running hotel's loopback health URL and can interrupt the drill. Production container IDs, image IDs, start times and health are compared before and after. Cleanup verifies removal of the current run's Docker resources.

Run the explicit server mode from a reviewed source archive extracted into an operator-owned directory under `/tmp` with a private `0700` parent. It requires an existing noninteractive `sudo docker` grant and the native ARM64 daemon. The fixture exercises cold worker starts from cached images.

```sh
python3 scripts/smoke_speech_runtime.py --arm64-server --source "$PWD"
```

## Results

The 17 November 2026 run completed in **84 seconds**. The source archive matched commit `66381b81adcf6c20bad7541dd802e4027b63bde9` and SHA-256 `82dc0ec9317bf25bbd98425f9a3fe82afe6413c608a64ca2d07679883ec08107` after transfer.

| Check | Observed result |
| --- | --- |
| Host preflight | 2 logical CPUs, 9,983.3 MiB available RAM and 90,633.5 MiB free on Docker storage |
| Startup and recovery | Native preflight passed. Private Django admin recovered the seeded pending operation at revision 0. |
| Provider switches | English TTS at revision 1, Polish TTS at 2, English STT at 3, Polish STT at 4 |
| Guest speech and drafts | Real English and Polish Piper/Vosk requests passed during split routes and after the final switch. Both fictional booking drafts remained intact. |
| Production health | 115 successful loopback health samples, p95 4.28 ms, maximum 42.25 ms |
| Worker states and cleanup | Original staging workers stopped when unreferenced. The replacement workers served the final routes. The run removed staging containers, network and temporary files. |
| Production service comparison | All three production service IDs, image IDs, start times and health matched before and after. |

The timings describe the loopback health route during this single short drill. The admin test client used a fixture login and the server's private controller connection. Production activation still requires selecting the final private admin topology, verifying backup and rollback, and configuring the approved production workers and their limits.
