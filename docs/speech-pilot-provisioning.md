# Prepare the production speech pilot

Run the preparation command on the native ARM64 server from a checksum-verified, root-owned release under `/opt/hotel-ai-speech`. Create the release archive with `git -c tar.umask=0022 archive` to exclude group write access. Keep the existing fixed speech stack serving during preparation. The command creates the private controller account and stores, records rollback inputs, and creates the four routed workers in the stopped state.

```sh
sudo /usr/bin/python3 -B -E scripts/provision_speech_pilot.py --prepare-stopped --subnet PRIVATE_SUBNET
```

Supply a dedicated private IPv4 `/28` that is free of Docker and host route conflicts. Use the exact production Piper and Vosk image IDs already cached on the host. The separate staged configuration is for use with the existing production Django settings during the cutover.

| Location | Purpose |
| --- | --- |
| `/var/lib/hotel-ai-speech/operator/routed.env` | Root-only routed Compose settings and controller credential |
| `/var/lib/hotel-ai-speech/operator/rollback.json` | Known-good web and fixed speech deployment identities and original Compose locations |
| `/var/lib/hotel-ai-speech/state/` | Controller manifest and lifecycle journal |
| `/var/lib/hotel-ai-speech/traffic/` | SQLite route and traffic state shared with Django |

The controller account uses UID/GID `10001:10001` with login disabled. Docker group access is scoped to the controller or the preparation command. Read the controller credential from the private environment configuration when launching the process. Pass the routed environment file after the original Django environment file when applying the routed Compose overlay during cutover.

If preparation is interrupted, inspect the partial state before retrying. The explicit `--resume-identity` option covers an existing locked account and an empty root-owned operator directory before any worker or store has been created. Preserve the original traffic-store inode and the worker and bridge identities in the manifest. Before activation, confirm the prepared workers are still stopped and the fixed stack matches the rollback inventory. Tag the recorded known-good web image and prepare a separately pinned candidate Django image with the routed traffic and private admin integrations. Take and verify the booking database backup when writes are paused during the staffed window. [Offline initializer](speech-pilot-initialization.md) · [Production rollout](speech-production-rollout.md)

## Production verification

On 18 November 2026, the ARM64 server provisioned the stopped pilot from release `d56d1a4ccded8f551d3281d5016a7334bffc9425`. The archive SHA-256 was `8195ee44a64efda0867d721480b2e1107d10d7979e61ea3a8447122a304da933`. The initial attempt rejected group-write archive modes. Once corrected, account creation stopped on an unsupported `useradd` option. The locked account was created with the supported arguments and the reviewed explicit resume completed from the empty operator directory.

The account has UID/GID `10001:10001`, a locked password, a nologin shell, and only its primary group by default. All four workers are still in the Docker `created` state. The native startup check passed with no listener at port 8093. An independent read-only SQLite check confirmed four initial routes at revision zero with zero claims and journal operations. Both stores and the manifest have mode `0600`. The routed environment and rollback inventory have mode `0600` and belong to root.

All six production loopback health samples passed, with a maximum of 3.55 ms. The provisioner confirmed that the production container and booking volume identities matched its baseline. Public HTTPS returned 200 for reception and 404 for admin. The live activation window and the booking backup are pending.
