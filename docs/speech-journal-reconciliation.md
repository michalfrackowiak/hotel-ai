# Explicit journal reconciliation

The durable executor supports an explicit reconciliation operation for pending or recovery-blocked work. It remains an internal API exercised with test backends, not an admin reset button or a production Docker recovery tool.

A caller supplies a unique recovery ID, expected persisted revision, all four proposed routes, actor and reason. Routes must reference approved compatible deployments. Healthy journals cannot be reset through this operation. The backend must freshly verify actual route ownership, required language-model readiness, cleanup of unreferenced workers and guest draining/resumption state before accepting the proposal. An absent, failing or negative verifier leaves the recovery block intact.

Successful reconciliation increments the revision, records the actor/reason and prior/new states, resolves pending request outcomes, and clears the block in one SQLite transaction. Existing completed outcomes remain historical records. Repeating the same recovery ID and payload returns its recorded outcome without repeating verification; changing the payload conflicts. Retrying a resolved original request returns the reconciliation outcome instead of replaying its interrupted effects.

## Exclusive ownership

Execution and reconciliation hold a nonblocking operating-system file lock across backend work and final persistence. Another instance cannot clear a pending operation while its original executor is still active. A process exit releases this lock, but the durable pending record still blocks new switches until explicit reconciliation.

The sibling lock file is stable and must not be deleted or replaced while any process uses the journal. Keep the database and lock file together in a private local directory. Use the same canonical path in all processes; hard-link aliases, separate database copies and network filesystems are unsupported. Stop all older executor processes before upgrading the journal, and run only versions that honor this lock. This does not coordinate older versions or external tools that bypass the executor.

## Limits

Audit history is bounded and not automatically evicted. A failed final write leaves the prior block and audit state intact. Actor/reason are caller-provided audit metadata, not authenticated identities; a future control endpoint must authorize the caller and supply trustworthy actor information.

The verifier contract is mandatory but its real Docker/guest implementation is still pending. No automatic repair, container command, guest-route publication or public mutation endpoint is added. Do not clear blocks by deleting the journal or lock file.
