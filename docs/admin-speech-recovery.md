# Explicit speech recovery from admin

The live controller status page links to recovery when `HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=1` and the staff member has the **Recover the live speech controller** permission plus all four speech catalog view permissions. Recovery is disabled by default. The separate control URL and token must already be configured as described in [admin status](admin-speech-status.md).

## Review, save, then send

1. Open recovery to fetch the current controller status. It must be blocked and have no existing controller recovery ID. Review the journal revision and the four observed routes.
2. Enter a reason and confirm the reviewed observation. The signed preview expires after five minutes and is tied to the staff member, configured controller URL and recovery ID. Submitting it again opens the saved request while the preview is valid.
3. Submit the preparation form to save a recovery record in Django. This step does not send a recovery command.
4. Review the saved record and explicitly confirm sending it. The controller independently verifies routes, claims, worker identity, readiness and resource capacity before reopening traffic.

The database record retains its UUID, original controller URL, exact recovery payload, creator, creation time, last attempt time and latest returned outcome. The attempt timestamp is committed before dispatch. No database transaction spans the network request. The controller's audit actor identifies the configured service credential; the Django record identifies the staff member who prepared the request.

All forms require staff authorization and CSRF-protected POST requests. GET requests do not create records or dispatch commands. Neither the payload nor controller destination can be edited through the admin flow. A conditional database constraint permits only one unresolved request per controller URL. This prevents concurrent preparations from creating competing recovery IDs.

## Lost responses and manual retries

The transport makes one authenticated POST attempt, with a 20-second helper-process budget plus operating-system startup/reaping overhead. A controller recovery can take longer because it verifies multiple models. Timeout, an invalid response or a disconnected client therefore means **outcome unconfirmed**, not failed recovery. The controller may still be executing or may already have completed.

Refresh live status and retry the **same saved record** when appropriate. Every retry sends the original recovery ID, revision, observation and reason; the controller rechecks pending recovery or returns its completed historical result. No automatic retries occur. A validated `reconciled` response matching the saved observation marks the record completed; subsequent confirmation does not resend it. Concurrent responses cannot overwrite an already completed record.

The configured controller URL must still match the saved URL before a retry. Restoring connectivity or rotating the token is allowed; changing the destination does not redirect an existing request. A URL is not cryptographic server identity: keep the tunnel and endpoint configuration under trusted operator control.

## Unresolved and rejected requests

Rejected or recovery-required results remain unresolved and visible. A stale revision or conflicting observation may require offline operator investigation; repeatedly submitting the same stale payload will not fix it. This slice has no discard, reset or payload-edit action, including for a prepared but unsent request. Such a record blocks new recovery preparations for that endpoint until reconciled or resolved through a separately reviewed offline procedure. Do not delete records or issue fresh IDs to bypass uncertain controller work.

An existing controller recovery that has no matching Django record also requires operator investigation. Use the separate [provider switching workflow](admin-speech-switch.md) to apply planned assignments after the controller is healthy.

## Migration and rollout

Migration `0004_speechrecoveryrequest` adds the audit table, custom permission and unique unresolved-request constraint. Existing planning selections are not changed. Back up Django state before migration; reverting the migration would remove recovery history and is unsafe while requests remain unresolved.

Tests cover real HTTP dispatch, timeouts and response validation, saved-before-send ordering, signed-preview tampering, permissions/CSRF, unchanged retry payloads, destination changes and completed-result protection. This change is not deployed and does not enable the feature in production. Complete runtime staging remains required before enabling it for staff.
