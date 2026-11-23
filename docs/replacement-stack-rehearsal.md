# Replacement-stack recovery rehearsal

This is the next increment after the [off-host database drill](offhost-recovery.md).
It exercises an isolated Windows Docker Desktop replacement stack using fictional booking data.
Keep the source backup outside the test runtime and run the application on a separate restored
copy. Probe Gunicorn on loopback and exercise both speech workers with synthetic input.
Production data, delivery credentials and Google credentials are outside this fixture.

## Workstation preflight

On 24 November 2026, the connected Windows 11 Pro workstation reported an Intel Core
i5-8279U with four physical cores and eight logical processors, about 7.85 GiB usable RAM,
and 25.8 GiB free on C:. Docker reported x86-64, eight CPUs and 3.75 GiB assigned memory.
Windows reported about 628 MiB of available physical memory at that check, and existing
Docker workloads were active. These figures describe the observed Windows environment.
A separate macOS runtime and physical microphone remain to be verified.

## Scope

The rehearsal can verify startup from a restored SQLite database, loopback HTTP and bounded
Piper and Vosk calls on a second architecture. Full recovery still requires a target host,
restored deployment configuration and credentials, the routed controller's persistent state,
TLS and DNS cutover, and measured recovery time and backup age.

## Acceptance checks

- The original fictional snapshot keeps its checksum. SQLite integrity, foreign keys and
  migration compatibility pass before the restored database enters the runtime.
- A booking created before snapshotting remains available after startup. The web stack
  binds only to loopback and serves health, reception and admin login.
- English and Polish both receive nonempty Piper WAV output and Vosk accepts microphone
  PCM silence through the corresponding reception routes. This does not measure recognition
  accuracy or validate a physical microphone.
- The rehearsal removes its own containers, network and database volumes after verification.

## Run the rehearsal

Build or load the matching web, Piper and Vosk images first. Pass their local Docker image IDs
to avoid depending on mutable tags. The script uses the selected Docker daemon, creates
uniquely named test resources, and uses only cached images during the rehearsal.

```powershell
python scripts/rehearse_replacement_stack.py --web-image <web-id> --piper-image <piper-id> --asr-image <vosk-id>
```

The fixture creates its own fictional snapshot on the workstation. It mounts the snapshot
read-only during recovery and starts Gunicorn with only the restored database. An internal
Docker network connects the speech workers. The HTTP checks run on loopback inside the web
container, without publishing a port to the workstation. The script prints aggregate checks
and times along with the resource prefix and cleanup result.

## Windows result, 24 November 2026

The x86-64 web, Piper and Vosk images built on Docker Engine 29.9.3. The fictional snapshot
passed SQLite integrity, foreign keys, source and restore row-count agreement, migration
compatibility, English and Polish reception entry and fictional booking replay. All verifier
writes rolled back and the original checksum matched. The verifier covered 37 tables in
8.95 seconds. Gunicorn booted and listened on its container loopback.

Available Windows physical memory fell to 1 MiB with all three workers started. The run
was stopped before the HTTP and speech checks completed. The script now requires at least
2 GiB of available Windows memory before setup and again before worker startup. Docker caps
remain one CPU per container and 256 MiB for web, 384 MiB for Piper and 768 MiB for Vosk.
The hosted Linux job exercises the full fixture with the already-built CI images.
