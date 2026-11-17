# Live speech provider switching in admin

Use **Speech selections → Live controller status → Review planned provider switches** to apply a planned assignment to one of the four English and Polish STT and TTS slots. An eligible target is the saved planned deployment for that slot, on the configured controller host label, with a provider that supports the language and task. The controller independently checks its own approved deployments, current routes, resource admission, endpoint binding and readiness.

| Django process setting | Value |
| --- | --- |
| `HOTEL_SPEECH_CONTROL_SWITCH_ENABLED` | Set to `1` to enable switching. It defaults to `0`. |
| `HOTEL_SPEECH_CONTROL_HOST` | The exact planning host label associated with this private controller. |
| `HOTEL_SPEECH_CONTROL_URL` and `HOTEL_SPEECH_CONTROL_TOKEN` | Private loopback control connection and its credential. |

The feature requires active staff access, the four speech catalog view permissions and **Switch the live speech controller provider**. The settings belong in the Django process environment. For staging in Docker, inject them privately and establish the loopback control connection in Django's network namespace. See [admin status configuration](admin-speech-status.md).

## Review and confirm

1. Open provider switching to compare each live route against the planned assignment. An eligible route must differ from the plan and the status must show agreement between the journal and actual routes, with switching unblocked.
2. Save the proposed switch for the selected slot. The preview is signed for the staff member, controller, plan and live revision, and expires after five minutes. If the plan changed before the preview was submitted, fetch a fresh preview.
3. Open the saved request and confirm dispatch. Its payload and request ID stay fixed. You can cancel a saved request before its first send.

The saved request records its creator, original target, intended target, last attempt and latest controller result. Preparing or sending a switch leaves the planned assignment as a separate record. The controller changes live admission and routing for the requested slot and retains workers still shared by other slots.

## Outcomes and retries

The admin records the attempt before sending one bounded request. A missing or invalid response leaves the attempt unconfirmed. The 20-second transport budget can expire while the controller is still working. Check live status and explicitly retry the same saved request. Controller replay uses its original ID and payload. The saved destination and planning host label must still match the current configuration.

Controller results describe what happened to the saved request. For the current selection, refresh live status. A rollback, rejection, cleanup requirement or recovered journal entry has its own outcome. A definitive controller result closes the admin attempt. A pending or uncertain result keeps the request available for retry. A blocked controller still requires the separate recovery workflow. See [admin recovery](admin-speech-recovery.md).

Migration `0005` adds the switch request table and permission. One unresolved switch per controller URL keeps retries discoverable even after a browser reload. Keep the table when rolling back application code so audit records and request IDs are retained. Full runtime staging with real speech workers and the private admin connection remains the rollout gate.
