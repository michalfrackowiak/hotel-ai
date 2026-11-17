"""Durable routed switching; uncertain operations require coordinated recovery."""
from dataclasses import asdict

from .backend import DockerLifecycleBackend
from .journal import DurableLifecycleExecutor, JournalUnavailable, encode
from .lifecycle import State, SwitchResult


class RoutedLifecycleExecutor(DurableLifecycleExecutor):
    _routed_execution = True

    def __init__(self, backend, deployments, routes, path, *, revision=0, journal_limit=128):
        deployments = tuple(deployments)
        if not isinstance(backend, DockerLifecycleBackend) or backend.routed_traffic is None:
            raise ValueError("Routed execution requires a backend bound to routed traffic.")
        self._traffic = backend.routed_traffic
        catalog = lambda items: sorted((item.name, item.task, sorted(item.languages)) for item in items)
        if (catalog(deployments) != catalog(self._traffic.deployments)
                or catalog(deployments) != catalog(backend.deployments)):
            raise ValueError("Routed traffic and lifecycle deployment catalogs must match.")
        super().__init__(backend, deployments, routes, path, revision=revision, journal_limit=journal_limit)

    def _journal_identity(self, catalog):
        return dict(mode="routed-v1", catalog=catalog, traffic=self._traffic.binding)

    def reconcile(self, *args, **kwargs):
        raise JournalUnavailable("Routed recovery requires coordinated route and traffic reconciliation.")

    def _checkpoint(self, request, payload, prior, destination):
        """Record the route decision while retaining an unfinished operation."""
        with self._transaction() as db:
            state, blocked = self._load(db)
            pending = db.execute("SELECT request,outcome FROM operations WHERE request_id=?", (request.request_id,)).fetchone()
            if state != prior or blocked or pending != (payload, None):
                raise ValueError
            db.execute("UPDATE metadata SET state=?,blocked=1 WHERE id=1", (encode(asdict(destination)),))

    def _complete(self, request, payload, result):
        with self._transaction() as db:
            state, blocked = self._load(db)
            pending = db.execute("SELECT request,outcome FROM operations WHERE request_id=?", (request.request_id,)).fetchone()
            if state != result.state or not blocked or pending != (payload, None):
                raise ValueError
            remains_blocked = result.status in ("recovery_required", "cleanup_required")
            db.execute("UPDATE metadata SET blocked=? WHERE id=1", (int(remains_blocked),))
            db.execute("UPDATE operations SET outcome=? WHERE request_id=?", (encode(asdict(result)), request.request_id))
        return result

    def _run_pending(self, request, payload, state):
        def finish(status, reason):
            result = SwitchResult(status, reason, state)
            self._finish(request, payload, state, result)
            return result

        if request.expected_revision != state.revision:
            return finish("rejected", "stale_revision")
        approved = {item.name: item for item in self._deployments}
        target = approved.get(request.target)
        if target is None or target.task != request.slot[1] or request.slot[0] not in target.languages:
            return finish("rejected", "unapproved_or_incompatible_target")
        # A mismatch is already uncertain; never clear the pending journal or
        # mutate a worker on the basis of one store's version of the routes.
        try:
            if self._traffic.state != state:
                self._traffic.drain(request.slot)
                return SwitchResult("recovery_required", "route_state_mismatch", state)
        except Exception:
            return SwitchResult("recovery_required", "route_state_unavailable", state)
        routes = dict(state.routes)
        old = routes[request.slot]
        if old == request.target:
            return finish("unchanged", "already_selected")
        destination = State(state.revision + 1, tuple(sorted((routes | {request.slot: request.target}).items())))
        try:
            if self._backend.revalidate(state, destination) is not True:
                return finish("rejected", "admission_denied")
        except Exception:
            return finish("rejected", "admission_unavailable")
        started = False
        try:
            self._backend.drain(request.slot)
            if self._backend.revalidate(state, destination) is not True:
                raise RuntimeError("Admission changed while draining.")
            if request.target not in routes.values():
                started = True
                self._backend.start(request.target)
            if self._backend.ready(request.target, request.slot) is not True:
                raise RuntimeError("Replacement is not ready.")
        except Exception:
            return self._rollback_pending(request, payload, state, old, started)
        # A failed publication may have committed before its acknowledgement
        # failed. Never stop the replacement or reopen the slot in this case.
        try:
            if self._traffic.publish(state, destination) is not True:
                return SwitchResult("recovery_required", "publication_uncertain", state)
        except Exception:
            return SwitchResult("recovery_required", "publication_uncertain", state)
        self._checkpoint(request, payload, state, destination)
        if old not in dict(destination.routes).values():
            try:
                self._backend.stop(old)
            except Exception:
                return self._complete(request, payload, SwitchResult("cleanup_required", "old_worker_cleanup_failed", destination))
        return self._resume_pending(request, payload, destination, "succeeded", "switched")

    def _rollback_pending(self, request, payload, state, old, started):
        try:
            if self._traffic.state != state:
                return SwitchResult("recovery_required", "route_state_mismatch", state)
        except Exception:
            return SwitchResult("recovery_required", "route_state_unavailable", state)
        safe = True
        if started:
            try:
                self._backend.stop(request.target)
            except Exception:
                safe = False
        try:
            safe = self._traffic.state == state and self._backend.ready(old, request.slot) is True and safe
        except Exception:
            safe = False
        self._checkpoint(request, payload, state, state)
        if not safe:
            return self._complete(request, payload, SwitchResult("recovery_required", "rollback_incomplete", state))
        return self._resume_pending(request, payload, state, "rolled_back", "replacement_failed")

    def _resume_pending(self, request, payload, state, status, reason):
        try:
            if self._traffic.state != state:
                raise RuntimeError("Route changed before resumption.")
            self._backend.resume(request.slot)
        except Exception:
            # A wrapper may raise after reopening admission; close it again.
            self._traffic.drain(request.slot)
            status, reason = "recovery_required", "resume_failed_after_checkpoint"
        return self._complete(request, payload, SwitchResult(status, reason, state))
