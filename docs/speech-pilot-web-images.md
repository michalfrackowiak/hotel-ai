# Pinned Django images for the speech pilot

Apply `compose.web-image.yaml` last to select an already cached Django image. Set `HOTEL_WEB_IMAGE` to the full `sha256:` image ID verified on the server. The overlay removes the inherited build and disables pulls. It preserves the Compose project and booking volume.

For the routed pilot, use `compose.yaml`, `compose.server.yaml`, `compose.speech-routed.yaml`, then `compose.web-image.yaml`. Pass the existing Django environment file first and the private routed environment file second. For rollback, use the original fixed stack's Compose files and Django environment file, followed by `compose.web-image.yaml` and the verified rollback image ID. The original fixed stack includes both the Piper and ASR overlays. Validate quietly with `docker compose config -q` before changing containers. [Production rollout](speech-production-rollout.md)

## ARM64 verification

The candidate was built for Linux ARM64 from the tracked source at `0ae6dc1c437af3dba6ddf55b2ffcfa0401de52fa`. The server verified the transferred image archive SHA-256 `9271920bfa6957dddb6dffa9c62a7000a8212fc8432478a1a213053477f22121` and matched the loaded image ID and source revision. The rollback image matched both the running web container and the recorded preparation inventory. Both images have local retention tags. Select them by full ID from the operator record for cutover.

The candidate passed Django checks, migration drift checks, and migrations against a fresh tmpfs SQLite database. Gunicorn served health, English and Polish reception, admin login, and admin static files on container loopback. The disposable container used no network or production mounts. Gunicorn logged a control-socket write error under the fixture's read-only home and still served every requested route. The first transfer attempt stopped before image load because stopped workers lack a health record. The corrected comparison confirmed the same seven container IDs, image IDs, start times, and states after cleanup, and production loopback health passed.

Both configurations passed quiet validation on the server with their actual environment files. The verified overlay and image IDs are in the root-only operator directory at `/var/lib/hotel-ai-speech/operator/compose.web-image.yaml` and `/var/lib/hotel-ai-speech/operator/web-images.json`.
