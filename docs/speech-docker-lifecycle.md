# Docker lifecycle composition

The lifecycle backend connects the durable executor to approved Docker controls. It requires explicit admission, traffic-gating, language-specific readiness and reconciliation implementations; there are no production defaults that simply return success.

Before a switch, the backend inspects approved workers and requires every currently referenced worker to be running. A newly targeted, unreferenced worker must be stopped; it cannot silently take over a running worker outside route ownership. Approval names must match the lifecycle deployment catalog.

Readiness requires both a running approved container and the injected model-readiness check for the affected language/task. Docker state alone is insufficient. Drain and resume must explicitly acknowledge completion. Recovery verification requires proposed workers running, unreferenced approved workers stopped, each language/task ready and the external verifier confirming actual guest routing and traffic state.

## Verification scope

CI uses three disposable, network-isolated, read-only containers with dropped capabilities and fixed resource limits. It switches one language away from a shared worker, verifies the other language retains the old worker, moves the final reference and verifies the old worker stops. It also reopens the durable journal for a retry and exercises a failed readiness check with rollback.

These fixtures run sleeping Python processes, not speech models. Their injected admission, traffic gate and readiness checks are test implementations. The test verifies Docker/lifecycle/journal composition, not model quality, physical capacity, guest draining or public routing.

## Remaining production integration

The HTTP controller remains read-only and no admin switch button is exposed. A production backend still needs authoritative resource admission from immutable policy, bounded language-model probes, actual guest admission/draining and atomic routing publication, plus authenticated operation dispatch. The durable journal and controls must share stable ownership and verified daemon configuration. No production deployment is included.
