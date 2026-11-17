# Audited routed-switch recovery

`RoutedLifecycleExecutor.recover(recovery_id, expected_revision, observed, actor, reason)` is an explicit internal operator action for blocked or interrupted routed switches. `observed` is the exact `State` read from the shared route store. Recovery adopts that verified state; it does not start/stop containers, rewrite provider routes or discard speech claims.

## Verification and ordering

Recovery shares the switch journal's exclusive execution lock. It rejects stale journal revisions, conflicting recovery IDs, route rollback, same-revision route disagreement, and healthy journals that do not need recovery. All four slots close together and drain within a bounded wait. Active or uncertain claims prevent recovery; no age-based expiry or force-clear option exists.

The route must match the requested observation. Referenced approved workers must be running, unreferenced approved workers must be stopped, every language/task must pass readiness, and the configured reconciliation verifier must explicitly approve. Endpoint/container binding and operational topology remain responsibilities of that verifier and trusted local configuration.

Before reopening admission, the coordinator writes the observed route into the journal, retains its blocked state, and records a pending recovery audit with actor, reason, prior journal state and pending request IDs. It then reopens all four slots atomically only if the route still matches and no claims exist. A final transaction completes the audit, resolves unfinished switch records and clears the blocked state. Previously completed outcomes remain unchanged.

## Recovery interrupted during recovery

The route/traffic store and journal are separate databases; this sequence is not a distributed transaction. Validation rejections leave admission unchanged. Once all slots have closed, failure before the audit checkpoint keeps them closed. Failure after checkpoint leaves a durable selected route and a pending recovery. Failure to persist the final audit after resumption may leave traffic open on the checkpointed route, while further switches remain blocked.

Retry the **same recovery ID with the same arguments**. A pending retry closes all slots again and repeats route, claim, worker and readiness verification; it never blindly resumes. Completed retries return the historical result. A different recovery ID cannot bypass an unfinished recovery. Pending operations and audit data survive restarts.

If verification fails because of outstanding claims, wrong worker state or unavailable models, fix the underlying condition through the controlled offline operational process. This API provides no automatic container repair, claim deletion or bypass of the verifier. Caller-supplied actor text is an audit label, not authentication. Authenticated dispatch and admin recovery controls are still required before exposing this action.

## Deployment boundary

Use one serialized journal/controller owner per route store, with identical verified catalogs and private local storage. Keep older controller processes stopped during upgrades. The legacy `reconcile` method remains unavailable on routed executors; this method handles routed recovery instead. Existing audit tables are reused without a schema migration.

Tests cover publication-gap adoption, uncertain claims, stale/conflicting requests, verifier denial, crash/retry and failed SQLite writes. CI interrupts a real Docker fixture after route publication, recovers the verified route with the new API, checks historical replay and confirms all slots admit the expected targets afterward. Its readiness and external verifier remain fictional. No production deployment is included.
