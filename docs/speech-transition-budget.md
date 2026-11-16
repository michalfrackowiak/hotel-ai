# Speech transition budget preview

Open **Planned speech assignments → Check transition budget** in the private admin to compare a complete destination plan with a fresh controller inventory and estimate configured CPU/RAM overlap. The static preflight checks destination allocations alone; this preview additionally retains observed active containers, so a plan that fits at steady state can still exceed its reserved budget during transition. The page requires the same four speech view permissions as the inventory page, accepts GET only and is not cached.

This is read-only planning, not an activation permit. It does not stop workers, create containers, change routes, reserve resources, or determine which provider currently serves a language.

## Accounting

- Count each distinct planned deployment once, including bilingual workers shared by multiple slots.
- Retain all observed running, paused, restarting or removing containers, including containers with no planning record. Do not assume an old worker can be stopped to make room.
- Reuse an observed container only when the host/name, configured image reference and CPU/RAM limits match the plan. A conflicting name blocks the report rather than authorizing replacement.
- Count a planned worker absent from the snapshot as additional allocation. Absence does not prove the name is available: the controller sees only its allowlist, and actual creation requires reconciliation.
- Created, exited and dead containers contribute no active allocation unless the destination plan needs them.
- Unknown active limits block the report. All planned hosts must be covered by the single configured controller host; cross-host plans require a later multi-host implementation.

Existing static-preflight blockers remain blockers. Totals are compared with the locally approved reserved speech budget, not the host's physical capacity. CPU observations reflect Docker `NanoCpus`, not every possible CPU constraint. Snapshot freshness and schema validation use the existing controller client.

## Limits and next step

When configured overlap checks pass, the page also runs the separate [resource preview](speech-resource-admission.md) against the same snapshot. That section requires local reserves, artifact estimates and operator-confirmed topology; it defaults to blocked independently of the configured-budget result.

A passing overlap preview means only that the configured allocations fit the reserved budget under this conservative accounting. It does not establish free memory, disk availability, model readiness, artifact integrity, active route ownership or safe execution order. It cannot detect workers outside the controller allowlist or prevent the state changing immediately afterward.

The future controller must revalidate immutable execution inputs against actual host resources, serialize changes, drain in-flight speech, preserve shared workers, verify replacement readiness and restore the old route on failure. The snapshot and preview must never be used as a durable authorization token for activation.
