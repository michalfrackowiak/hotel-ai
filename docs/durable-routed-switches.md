# Durable routed speech switches

The routed coordinator connects approved Docker lifecycle operations to the shared route/traffic store and a separate durable switch journal. It uses the same routed traffic gate as the Docker backend and validates the deployment catalog. The journal is bound to routed mode and the specific route store/catalog; it cannot be silently reused by the legacy executor or a replacement store.

## Commit before resumption

1. Serialize the switch and persist its pending request before backend effects.
2. Verify journal/route agreement and admission, drain the affected slot, revalidate resources, start the replacement if needed, and check language-specific readiness.
3. Publish the new route while the slot is closed and empty.
4. Durably checkpoint the selected state in the journal, leaving the operation pending and blocked.
5. Stop the old worker only when no route references it, then resume the affected slot.
6. Persist the final outcome and release the operation lock.

Shared workers remain running for their other language/task references. Repeated completed requests return the stored outcome without repeating backend operations. Incompatible targets, stale revisions, route mismatch and incomplete prior operations block switching.

## Interruption boundaries

Route publication and journal writes are separate SQLite transactions. The implementation does not claim a distributed atomic commit. Their ordering and durable pending record make interruption visible and prevent automatic replay:

- Failure before publication can clean up an attempted replacement, verify the previous worker and checkpoint the previous route before resumption.
- A false, exceptional or interrupted publication is treated as uncertain. Both workers are retained and admission stays closed; the coordinator does not assume the new route was never committed.
- A failure to checkpoint after publication leaves the slot closed and the pending operation blocking further switches. The route store may be ahead of the journal and must be reconciled explicitly.
- A crash after checkpoint leaves the selected route durably known with a pending operation. No automatic retry performs cleanup or resumes traffic.
- Old-worker cleanup failure keeps the new route selected but admission closed, records the cleanup-required outcome and blocks further switches.
- Failure to store the final result after resumption leaves a durable selected-route checkpoint and pending operation. Requests already resumed may continue on that recorded route, but further switches remain blocked.

The routed coordinator refuses the legacy reconciliation API. Its separate [audited recovery method](routed-switch-recovery.md) adopts exact verified route state, requires empty drained slots and verified workers, and records a durable recovery checkpoint before resumption. It does not repair containers or remove claims. Never clear pending operations or uncertain claims merely to permit a retry.

## Scope and verification

This is an internal coordinator, not an authenticated control endpoint or admin switch action. Operators still must verify endpoint-to-container binding, native host topology, approved policy and shared private storage. All participating web/controller processes must use matching route catalogs. One journal and serialized control owner must manage each routed store: different journal paths have independent locks and must not control the same store concurrently. Older executors must be stopped before changing controller mode; the code rejects unsafe legacy use of routed backends.

Tests inject failures around publication, checkpointing, cleanup, resumption and final persistence using real disposable SQLite stores. CI composes the coordinator with approved Docker controls, resource admission and the real routed traffic store, using constrained sleeping-process containers and fictional readiness. These fixtures verify sequencing and shared ownership, not actual provider replacement quality or production recovery. No deployment is included.
