# Durable lifecycle journal

The optional durable executor wraps the lifecycle core with a separate SQLite operation journal. It is not the hotel's guest database and is not connected to the inventory HTTP service, Docker mutation commands or guest routing.

The journal persists the route revision, completed request outcomes and recovery blocks. Before any backend call it commits a pending request. Only one request may be pending across executor instances sharing the same database. There is no expiry lease that would permit a second executor to take over an uncertain operation.

Completed retries with the same key and payload return the saved result without replaying backend actions. A reused key with a different payload is rejected. Route state and final outcome are committed together. A crash or failed final journal write leaves an uncertain pending request that blocks further execution after restart. Recovery-required outcomes remain blocked across restarts.

Approved deployment capabilities are fingerprinted to reject accidental configuration changes when reopening a journal. Restart initialization never replaces persisted routes with the caller's initial route mapping. The bounded request history is not silently evicted; reaching its limit blocks new requests.

## Operations and remaining integration

Store the journal in a private, access-restricted local directory on reliable storage, separate from guest data. It contains operational routing metadata and must not be editable by untrusted processes. SQLite persistence is not tamper-proof and does not coordinate separate database copies or unreliable network filesystems.

Pending operations require [explicit audited reconciliation](speech-journal-reconciliation.md) against actual container and routing state. The internal API requires a backend verifier and exclusive journal access; no automatic retry or production recovery endpoint is available. Deleting the journal to clear a block can replay actions or discard route ownership; it is not a recovery procedure.

This is durable tracking of uncertain work, not automatic crash recovery. The next integration must implement authoritative verification, a pinned/allowlisted Docker backend with bounded calls, model readiness, guest draining and authoritative route publication. Until then the wrapper is exercised through test backends only and does not enable live switching.
