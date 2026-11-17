"""Single-process lifecycle core. No Docker adapter, HTTP endpoint or durable state.

Backend calls are synchronous and must implement their own bounded deadlines.
Revalidation is read-only; drain/resume gate the affected slot. A readiness result
must verify the required models, not just a running container. In-memory routes
are authoritative only inside this executor, not in the guest application.
Revalidation must reject unowned running targets or establish safe ownership and
verify approved artifacts, resources and topology before permitting a start.
"""
from dataclasses import dataclass
import re
from threading import Lock
from types import MappingProxyType


SLOTS = frozenset((language, task) for language in ("en", "pl") for task in ("stt", "tts"))
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


@dataclass(frozen=True)
class Deployment:
    name: str
    task: str
    languages: tuple

    def __post_init__(self):
        if (not identifier(self.name) or self.task not in ("stt", "tts")
                or type(self.languages) is not tuple or not self.languages
                or any(language not in ("en", "pl") for language in self.languages)
                or len(set(self.languages)) != len(self.languages)):
            raise ValueError("Invalid approved deployment.")


@dataclass(frozen=True)
class SwitchRequest:
    request_id: str
    expected_revision: int
    slot: tuple
    target: str

    def __post_init__(self):
        if (not identifier(self.request_id) or type(self.expected_revision) is not int
                or self.expected_revision < 0 or type(self.slot) is not tuple
                or len(self.slot) != 2 or any(type(value) is not str for value in self.slot)
                or self.slot not in SLOTS or not identifier(self.target)):
            raise ValueError("Invalid switch request.")


@dataclass(frozen=True)
class State:
    revision: int
    routes: tuple


@dataclass(frozen=True)
class SwitchResult:
    status: str
    reason: str
    state: State


class LifecycleExecutor:
    """One instance per controller; journal exhaustion blocks new request IDs.

    No crash/restart safety or cross-process coordination is provided. Recovery
    failures latch this instance closed; rebuilding it requires operator-led
    reconciliation, never automatic reset. Busy requests have no journal entry.
    """
    def __init__(self, backend, deployments, routes, *, revision=0, journal_limit=128):
        deployments = tuple(deployments)
        if any(not isinstance(deployment, Deployment) for deployment in deployments):
            raise ValueError("Approved deployments must be immutable Deployment records.")
        approved = {deployment.name: deployment for deployment in deployments}
        if (len(approved) != len(deployments) or type(revision) is not int or revision < 0
                or type(journal_limit) is not int or not 1 <= journal_limit <= 128
                or set(routes) != SLOTS):
            raise ValueError("Invalid lifecycle initialization.")
        for slot, name in routes.items():
            deployment = approved.get(name)
            if deployment is None or slot[1] != deployment.task or slot[0] not in deployment.languages:
                raise ValueError("Routes must reference compatible approved deployments.")
        self._approved = MappingProxyType(approved)
        self._state = State(revision, tuple(sorted(routes.items())))
        self._backend = backend
        self._journal = {}
        self._journal_limit = journal_limit
        self._lock = Lock()
        self._blocked = False

    @property
    def state(self):
        return self._state

    def _result(self, status, reason):
        return SwitchResult(status, reason, self._state)

    def execute(self, request):
        if not isinstance(request, SwitchRequest):
            raise ValueError("A validated switch request is required.")
        if not self._lock.acquire(blocking=False):
            return self._result("rejected", "busy")
        try:
            previous = self._journal.get(request.request_id)
            if previous:
                return previous[1] if previous[0] == request else self._result("rejected", "request_id_conflict")
            if len(self._journal) >= self._journal_limit:
                return self._result("rejected", "journal_full")
            result = self._execute(request)
            self._journal[request.request_id] = (request, result)
            return result
        finally:
            self._lock.release()

    def _execute(self, request):
        if self._blocked:
            return self._result("rejected", "reconciliation_required")
        if request.expected_revision != self._state.revision:
            return self._result("rejected", "stale_revision")
        deployment = self._approved.get(request.target)
        if deployment is None or deployment.task != request.slot[1] or request.slot[0] not in deployment.languages:
            return self._result("rejected", "unapproved_or_incompatible_target")
        current = dict(self._state.routes)
        old = current[request.slot]
        if old == request.target:
            return self._result("unchanged", "already_selected")
        next_routes = current | {request.slot: request.target}
        next_state = State(self._state.revision + 1, tuple(sorted(next_routes.items())))
        try:
            if self._backend.revalidate(self._state, next_state) is not True:
                return self._result("rejected", "admission_denied")
        except Exception:
            return self._result("rejected", "admission_unavailable")
        new_target = request.target not in current.values()
        start_attempted = False
        try:
            self._backend.drain(request.slot)
            if self._backend.revalidate(self._state, next_state) is not True:
                raise RuntimeError("Admission changed while draining.")
            if new_target:
                start_attempted = True  # Start may partially succeed before raising.
                self._backend.start(request.target)
            if self._backend.ready(request.target, request.slot) is not True:
                raise RuntimeError("Replacement is not ready.")
        except Exception:
            return self._rollback(request, old, start_attempted)
        self._state = next_state
        cleanup_failed = False
        if old not in next_routes.values():
            try:
                self._backend.stop(old)
            except Exception:
                cleanup_failed = True
        try:
            self._backend.resume(request.slot)
        except Exception:
            self._blocked = True
            return self._result("recovery_required", "resume_failed_after_commit")
        if cleanup_failed:
            self._blocked = True
            return self._result("cleanup_required", "old_worker_cleanup_failed")
        return self._result("succeeded", "switched")

    def _rollback(self, request, old, start_attempted):
        cleanup_failed = False
        if start_attempted:
            try:
                self._backend.stop(request.target)
            except Exception:
                cleanup_failed = True
        try:
            if self._backend.ready(old, request.slot) is not True:
                raise RuntimeError("Previous worker is not ready.")
            self._backend.resume(request.slot)
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            self._blocked = True
            return self._result("recovery_required", "rollback_incomplete")
        return self._result("rolled_back", "replacement_failed")
