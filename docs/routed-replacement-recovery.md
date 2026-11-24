# Offline recovery of routed speech on a replacement host

Use this design when the original traffic database cannot be reopened at its original
filesystem identity. The journal fingerprint includes the traffic database's resolved path,
device, inode and target catalog. A copied pair is evidence of the prior controller state.
A replacement pair needs its own binding and an explicit audit record linking it to that
evidence. See the [copied-state rehearsal](replacement-stack-rehearsal.md#routed-speech-state-on-a-replacement-filesystem).

The current [initializer](speech-pilot-initialization.md) prepares new revision-zero stores
with one bilingual primary for each task. It cannot preserve a prior revision or mixed
assignments. The [routed-switch recovery API](routed-switch-recovery.md) requires an already
bound store pair and resolves interrupted switches in that pair. The offline tools below
validate captured evidence and prepare a replacement pair. The disposable boot rehearsal
checks startup and verifies the disabled-dispatch boundary below.

## Preserve and verify the source evidence

1. Fence the source and replacement from guest traffic. Account for every process with
   access to either store, including Django workers, the controller and any operator jobs.
   For a planned capture, stop accepting new work, drain within a bounded wait and stop
   all writers before taking the pair. Capture both databases from the same quiescent
   point. If the source failed, prove it cannot resume writes before replacement work.
2. Preserve both databases, the prior approved manifest and the source traffic binding.
   Record the capture time, source host and release, source stat identity, SHA-256 of each
   artifact and the operator's reason. Verify both hashes against an independently retained
   trusted capture record after transfer. A hash supplied alongside the copied artifact
   alone cannot establish provenance. Keep originals under restricted access, keep
   credentials separate and inspect only disposable copies opened read-only. Exclude raw
   journal data from console logs and reports.
3. Require known schema versions, SQLite integrity and foreign-key checks, and the prior
   journal fingerprint computed from the captured binding and approved catalog. Check
   fingerprints using the source values, which can differ from the replacement file
   identity. Validate both decoded states and compare the same revision and all four
   language/task selections. Require compatible approved targets, open admission slots,
   zero retained claims, an unblocked journal, completed switch outcomes and no pending
   recovery checkpoint.
4. Stop on failed checks, provenance gaps, mismatched routes or revisions, or uncertain
   work. Preserve the artifacts for incident review. Use the existing reconciliation
   path only if the original bound stores can be restored and its preconditions hold.
   A new ID or a replacement filesystem does not resolve a prior uncertain operation.

## Validate the captured evidence

Run the read-only validator against the private, transferred database copies and a trusted
capture record retained independently of those copies.

```sh
python3 -m speech_controller.recovery_evidence \
  --traffic /private/recovery/traffic.sqlite3 \
  --journal /private/recovery/journal.sqlite3 \
  --capture /private/retained-capture.json
```

The capture JSON supplies `traffic_sha256`, `journal_sha256`, `source_traffic_path`,
`source_traffic_device`, `source_traffic_inode` and `targets`. Use the original canonical
absolute Linux traffic path and the original numeric device and inode. Copy the approved
`targets` list from the source manifest. Keep the capture record and its provenance under
operator control. The validator checks the supplied evidence, while source fencing and
establishing the capture record's provenance remain operator responsibilities.

A successful result reports the verified revision, four route selections and audit counts.
The validator opens no controller listener, calls no workers and initializes no SQLite stores.
Use a Python build with SQLite deserialization available. Inputs are limited to 32 KiB for the
capture record and 16 MiB per database. Provide standalone SQLite copies in rollback journal
format with no adjacent journal, WAL or shared-memory sidecars. Treat a failed result as an
offline refusal and preserve the source evidence for review.

## Prepare an isolated replacement pair

1. Keep the replacement controller listener, web writers and public ingress offline.
   Provision the target Docker daemon, private bridge and replacement workers with the
   approved images, capabilities and budgets. Verify full replacement container IDs,
   image IDs, bridge identity, private static addresses and resource limits against a
   newly reviewed manifest. Verify every approved worker is stopped. Check compatibility
   with each saved language/task route. If a provider identity needs to change, require an
   explicitly reviewed mapping and a separate acceptance decision.
2. Run offline preparation to validate the evidence and replacement manifest, then
   exclusively create both files in a new owner-private staging directory. Reuse the
   store constructors to seed the exact recorded revision and route map and bind the new
   journal to the new traffic file. Preserve the source journal as a separate immutable
   audit artifact. The new journal begins a new history at the recovered revision and
   must not present historical requests as replayable.
3. Write an operator recovery record with a unique ID, actor, reason, source hashes,
   source and destination revision and route selections, verified replacement identities
   and resulting store hashes. Protect and durably record it before making the new
   manifest eligible for service. Keep incomplete staging attempts isolated. Refuse
   existing output paths and reuse of a completed recovery ID with different evidence.
   A retry after incomplete staging uses fresh private output paths. If the ID already
   has a completed recovery record, verify it against the same evidence before accepting
   the prior result. Never point runtime configuration at a partially written generation.

### Run offline preparation

Use the dedicated native Linux controller account with both hosts fenced. Keep the source
evidence and the new manifest in private directories. The manifest must name fresh store
paths and the exact approved providers by their saved names and language/task capabilities.
Use the same canonical host-only audit directory for all attempts, outside the traffic mount.
Supply the existing controller token through private environment configuration.

```sh
python3 -m speech_controller.prepare_replacement \
  --config /private/replacement/controller.json \
  --traffic /private/recovery/traffic.sqlite3 \
  --journal /private/recovery/journal.sqlite3 \
  --capture /private/retained-capture.json \
  --audit-dir /private/replacement/audit \
  --recovery-id replacement-rehearsal-001 --reason "Disposable recovery rehearsal"
```

A successful invocation preserves the saved revision and all four selections in newly bound
stores and writes a recovery record under the supplied ID. Its journal starts new audit history.
The command verifies approved stopped workers and leaves them stopped. Keep the manifest outside
runtime configuration until the separate boot and replay checks are complete. A successful
record has status `prepared`. Keep incomplete or malformed records and their stores offline
for inspection. Use a fresh recovery ID and fresh output paths for a new attempt. To verify
an already prepared generation, compare the prior record and output hashes. The command
rejects reuse of a recovery ID, and runtime startup does not enforce the audit record.

## Verify readiness and rehearse failure

1. With traffic and writers still fenced, verify the newly bound pair with the native
   controller configuration check. Use the existing controlled boot gate to start only
   the workers selected by the saved routes. Verify identity and bounded inference on
   all four language/task slots. All other approved workers must stay stopped. Check
   authenticated controller readiness and confirm the recovered revision and routes.
2. Start Django on its private listener and verify English and Polish reception and
   speech, booking recovery and isolation from external delivery using fictional data.
   Recheck health before deciding whether to activate ingress. A rehearsed rollback
   keeps ingress fenced, closes the replacement writer and controller, stops replacement
   workers and retains both evidence generations. Reopening an original host is a
   separate decision requiring proof that it is the sole writer with verified state.
   Keep switch and recovery dispatch disabled during and after cutover until the restored
   Django request records have been reviewed and fenced. Old pending or retryable switch
   and recovery IDs must stay bound to their original journal; preserve completed outcomes
   for audit and prevent replay against the replacement pair.
3. The first disposable proof should use a nonzero revision and mixed assignments with
   completed audit records. Demonstrate refusal for a changed hash or fingerprint, store
   disagreement, retained claim, pending operation, wrong worker binding and interrupted
   staging. For each offline refusal, verify unchanged source hashes and no worker starts
   or published configuration. After successful boot, verify the recovered revision,
   exact four selections, archived history, selected-only boot and bilingual readiness.
   Verify stale request replay causes no route or worker changes, then check cleanup.
4. Before production use, perform the full rehearsal on a quiet replacement host and
   document recovery elapsed time, backup age at recovery and the tested limits. Plan a
   separate approved window for certificate issuance and renewal, DNS and ingress cutover,
   and any credentialed integration checks.


## Disposable boot rehearsal

The hosted native-Linux fixture captures the stopped source store pair, preserves its audit
history, and prepares new databases at the saved revision. The fixture retains the saved
provider names and creates four new disposable containers on a separate private bridge.
Replacement approvals pin the new full container and bridge IDs, addresses, images and
resource limits. The controller must reopen the new generation and start only the workers
selected by the saved routes. The original stopped workers stay available for rollback. Django resumes fictional drafts
and exercises English and Polish speech through the new traffic store.

The fixture first submits restored requests with dispatch disabled and checks that the
controller state and workers are unchanged. It then enables switch and recovery dispatch
in the disposable Django configuration under a fresh generation. Archived requests must
remain fenced at the restored route revision. A fresh English TTS switch to the replacement
standby and back exercises real dispatch and restores the selected routes. The separate
replacement-host run still requires credentialed TLS, DNS and elapsed recovery checks.


## Fence restored admin requests

Keep both dispatch flags off during recovery. Before considering re-enabling either
flag, set `HOTEL_SPEECH_CONTROL_GENERATION` to a new, unique identifier for this
controller generation. Use 1 to 64 ASCII letters, digits, underscores or hyphens.
Apply the migrations and configure the same value on every Django process that can
write to the restored booking database. Stop older instances before the cutover.

The value binds signed switch and recovery previews and their saved requests to the
Django dispatch generation. Existing records remain available for audit. A preview or
request from a different generation cannot dispatch and does not block new requests.
Verify denial of a restored preview and saved request for both workflows, then verify
a newly reviewed request on the disposable controller.

Keep the generation value with the replacement configuration. An unset value preserves
the original behavior for older installations. Do not clear the setting or reuse an
earlier generation when restoring the same database. This setting fences Django admin
dispatch. Keep the private controller listener and credentials restricted to approved
processes, and review any other dispatch callers before enabling writes.

Keep dispatch disabled and retain the new schema during application rollback. Older
application versions ignore the generation. The earlier uniqueness constraint allows
only one unfinished request per controller URL and cannot represent unfinished requests
from multiple generations. Plan a separate schema rollback with dispatch disabled and
preserve the original request records.
