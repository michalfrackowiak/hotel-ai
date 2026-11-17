# Production speech controller rollout

The ARM64 staging run verified the controller and admin workflow on the hotel server. This guide records the production topology and order for an operator-led cutover. The opt-in Compose overlay defines the web listener and the worker specifications. The rehearsal uses disposable hosted CI resources and fictional data. [ARM64 staging results](speech-runtime-arm64-staging.md) · [Routed Compose configuration](speech-cutover-config.md)

## Private connections and worker limits

| Process | Proposed connection |
| --- | --- |
| Nginx | Keep the existing public HTTPS guest route allowlist and upstream `127.0.0.1:8080`. |
| Django | Use the native Linux host network namespace with Gunicorn explicitly bound to `127.0.0.1:8080`. Point its healthcheck at the same port and omit Docker published ports for this service. |
| Staff browser | Keep using the SSH local forward to Django's loopback port for `/admin/`. |
| Controller | Run one native Linux controller on `127.0.0.1:8093`. Use a dedicated private credential and keep its Docker Unix socket on the host. |
| Speech workers | Use an isolated bridge with stable private IPv4 addresses and no published ports. Django and controller readiness must reach those same addresses. |

Keep one Django writer on the existing booking volume during cutover. Run Django as an unprivileged account. Provision a separate local traffic directory with an owner UID shared by the controller and container process, and mount only that directory for traffic claims. Keep the lifecycle journal, manifest, and host Docker control outside Django's mounts. Verify numeric ownership and permissions in an isolated rehearsal before changing the service definition. Keep the booking volume's existing ownership until the chosen UID is validated. The admin credential belongs in private process configuration.

Start with the two bilingual Piper and Vosk images already used by the hotel. Record the exact image, container and bridge IDs, fixed address, owner and resource limits for every approved deployment. The read-only server check found Django at `127.0.0.1:8080`, no controller listener on port 8093, and one CPU assigned to each healthy speech worker. Any replacement would put the overlap above the server's two-core admission limit. Base a production overlap budget on measured resource use and verify it with the chosen web and worker caps. The [ARM64 staging caps](speech-runtime-arm64-staging.md) were 0.4 cores for each speech worker and 0.25 cores for Django.

## Before cutover

1. Save the known-good Django image and effective Compose settings, the current fixed speech addresses, and the method to bring those workers online. Record the current listener and container identities. Keep the previous release available on the host.
2. Rehearse the exact Gunicorn bind and SSH admin path on a disposable loopback port. Verify external requests to the Django and controller ports cannot connect, public `/admin/` remains outside the Nginx allowlist, and Django has no Docker socket.
3. Prepare the dedicated account, stopped workers, route store and journal before the cutover window. Use one controller owner and one shared route-store file. [Production pilot preparation](speech-pilot-provisioning.md)
4. Pause writes for the cutover window. Take a private SQLite backup with the backup API, checksum it, and verify integrity, foreign keys and Django migration compatibility on a disposable restore. Rehearse candidate migrations on the restore when the candidate introduces new tables. Preserve signing configuration securely so active sessions can resume. [Database recovery drill](offhost-recovery.md)
5. Run the controller startup check and verify booking health, the SSH-only admin path, actual speech in both languages and current routes. The pilot overlay sets both staff action flags to zero. Enabling either flag requires a later configuration change after backup and rollback readiness review. Reserve an operator who can stop the cutover while health or route ownership is uncertain. [Controller startup check](speech-controller-startup.md)

## Return to fixed speech endpoints

1. Disable the switch and recovery flags and stop admission of new web work during the cutover window. Let in-flight speech and control operations complete and stop the controller cleanly. Confirm the owner lock has released.
2. Preserve the live booking database, route store, lifecycle journal and audit records. A web-configuration rollback can keep bookings created since the release. Avoid applying old migrations to the live database as part of this rollback.
3. Check actual worker state and quiescence. Restore the last verified fixed Piper and Vosk endpoints using approved workers and their readiness checks. Review pending operations and retained traffic claims before stopping or reusing affected workers. Aged claims and a timed-out request do not establish completion.
4. Start the known-good Django configuration with `HOTEL_PIPER_URL` and `HOTEL_ASR_URL` set to the verified fixed endpoints. Clear `HOTEL_SPEECH_TRAFFIC_DB` and `HOTEL_SPEECH_ROUTE_TARGETS` and leave both staff action flags disabled. Preserve the signing key, live booking database, and existing migrations. Use the previously verified web release or the current compatible release in fixed mode according to the failure.
5. Check production health, existing booking drafts where available, English and Polish voice, and the private admin path. Treat returning to routed operation as a separate recovery decision based on actual routes and workers.

Back up route and journal files after all their writers exit, and keep the backups with the corresponding manifest and image/worker identities. Copies preserve evidence for recovery. The routed journal pins the original route-store path, inode and catalog, so a byte-for-byte copy at a different path or inode cannot be activated by this procedure. Keep the original files intact. A lost route store or replacement host requires explicit reviewed recovery.

## Rehearsal

The hosted fixture exercises a booking backup and a new Django process configured for fixed speech after the controller stops. The [ARM64 listener rehearsal](speech-private-cutover-check.md) separately checks a real SSH-forwarded Django login and the proposed web process UID. The remaining production checks apply to the final listener, private controller account, shared traffic directory and approved worker identities during cutover.
