# Persistent routed speech startup

The controller unit runs the boot gate as the dedicated account with process-scoped Docker access. Its bounded post-start check authenticates to loopback and compares the controller's state and observed routes to the private traffic store. The web unit binds to the controller and Docker, so systemd waits for that check and stops web on controller loss. [systemd unit dependencies](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html) · [systemd startup ordering](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)

## Deployment inputs

- Install the reviewed release at the canonical, root-protected `/opt/hotel-ai-speech/boot` path. Place the two service files under `/etc/systemd/system`. Enable the web unit, which starts the controller dependency.
- Keep the existing controller manifest and SQLite files in place. Supply a root-only `/var/lib/hotel-ai-speech/operator/controller.env` containing the existing `HOTEL_SPEECH_CONTROL_TOKEN`. Keep the token in private configuration.
- Prepare the stopped, pinned routed web container in the staffed window. Apply `compose.speech-systemd.yaml` last, after the pinned image overlay, so Docker does not start web outside systemd. Use the original booking volume and the same traffic directory. Disable Docker live restore on the selected local daemon before switching ownership. [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
- Record the exact daemon, stopped container and image IDs and both mounts in root-only `/var/lib/hotel-ai-speech/operator/web.json`. Its fields are `daemon_id`, `container_id`, `image_id`, `booking_volume` and `traffic_dir`. Update the approval during a staffed change whenever the web container is recreated.
- Pause guest traffic and stop the existing web writer and transient controller before starting these units. Check the expected bilingual routes, loopback health, and private admin access before reopening traffic.

Both units use explicit operator restart after failure. Preserve uncertain controller state and use the existing recovery procedure before restarting web. The unit stops its approved web container and leaves speech worker state for review. The live pilot stays on the transient controller until a separate staffed installation. [Production rollout](speech-production-rollout.md)

## Disposable reboot check

Create a fresh Debian systemd VM with QEMU DMI product name `hotel-ai-systemd-rehearsal`. Add a root-owned mode-0600 file `/etc/hotel-ai-systemd-rehearsal` containing that same name. Copy the reviewed source to a guest directory that persists across reboots. The check installs the real units and service helpers and uses fictional Docker and controller processes. Run it only inside the disposable guest.

```sh
sudo python3 scripts/check_systemd_pilot_vm.py prepare
sudo systemctl reboot
# After reconnecting to the guest
sudo python3 scripts/check_systemd_pilot_vm.py after-reboot
```

The VM check tests systemd ordering, real reboot, authenticated status disagreement, and controller-loss propagation. Real speech inference and existing controller state recovery were separately verified on ARM64. [ARM64 boot gate run](speech-pilot-boot-gate.md)

On 19 November 2026, the Debian 12 QEMU guest verified source archive SHA-256 `7f8987caec448fea9cfc74795b5704bda337538d08f91ec65f46888a5a4fb12a`. Both units passed `systemd-analyze verify`. The kernel boot ID changed, the event log showed web stopped before the controller and started after authenticated readiness, and both units reached active state. Killing the controller stopped web. A subsequent authenticated blocked response prevented web from starting. All six helper tests and six Compose checks passed. Independent mutations caught mismatched controller state, an unsafe restart policy and missing web ownership.
