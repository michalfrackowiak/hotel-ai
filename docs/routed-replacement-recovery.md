# Offline recovery of routed speech on a replacement host

Use this design when the original traffic database cannot be reopened at its original
filesystem identity. The journal fingerprint includes the traffic database's resolved path,
device, inode and target catalog. A copied pair is evidence of the prior controller state.
A replacement pair needs its own binding and an explicit audit record linking it to that
evidence. See the [copied-state rehearsal](replacement-stack-rehearsal.md#routed-speech-state-on-a-replacement-filesystem).

The current [initializer](speech-pilot-initialization.md) prepares new revision-zero stores
with one bilingual primary for each task. It cannot preserve a prior revision or mixed
assignments. The [routed-switch recovery API](routed-switch-recovery.md) requires an already
bound store pair and resolves interrupted switches in that pair. A replacement-host
recovery command remains to be implemented and rehearsed.

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

## Prepare an isolated replacement pair

1. Keep the replacement controller listener, web writers and public ingress offline.
   Provision the target Docker daemon, private bridge and replacement workers with the
   approved images, capabilities and budgets. Verify full replacement container IDs,
   image IDs, bridge identity, private static addresses and resource limits against a
   newly reviewed manifest. Verify every approved worker is stopped. Check compatibility
   with each saved language/task route. If a provider identity needs to change, require an
   explicitly reviewed mapping and a separate acceptance decision.
2. Propose a prepare command that validates the evidence and replacement manifest, then
   exclusively creates both files in a new owner-private staging directory. Reuse the
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
   disagreement, retained claim, pending operation, wrong worker binding, interrupted
   staging and stale request replay against the new pair. For each refusal, verify unchanged source hashes and no worker starts or
   published configuration. For success, verify the recovered revision, exact four
   selections, archived history, selected-only boot, bilingual readiness and cleanup.
4. Before production use, perform the full rehearsal on a quiet replacement host and
   document recovery elapsed time, backup age at recovery and the tested limits. Plan a
   separate approved window for certificate issuance and renewal, DNS and ingress cutover,
   and any credentialed integration checks.
