"""Lifecycle composition; production admission, traffic and model probes are required.

Callbacks must be authoritative and implement bounded deadlines. No permissive
defaults or guest-routing implementation are provided by this module.
"""
from types import MappingProxyType

from .lifecycle import Deployment, LifecycleExecutor
from .admission import ControllerAdmission


class BackendUnavailable(Exception):
    pass


class DockerLifecycleBackend:
    def __init__(self, controls, deployments, *, admission, traffic, readiness, reconciliation):
        deployments = tuple(deployments)
        if (not deployments or any(not isinstance(item, Deployment) for item in deployments)
                or len({item.name for item in deployments}) != len(deployments)
                or frozenset(item.name for item in deployments) != controls.approved_names
                or isinstance(admission, ControllerAdmission) and admission.controls is not controls
                or not all(callable(callback) for callback in (
                    admission, readiness, reconciliation,
                    getattr(traffic, "drain", None), getattr(traffic, "resume", None)))):
            raise ValueError("Matching approvals and explicit lifecycle integrations are required.")
        self._controls = controls
        self._deployments = deployments
        self._admission = admission
        self._traffic = traffic
        self._readiness = readiness
        self._reconciliation = reconciliation

    def _routes(self, state):
        validated = LifecycleExecutor(None, self._deployments, dict(state.routes), revision=state.revision).state
        if validated != state:
            raise ValueError("Invalid lifecycle route state.")
        return dict(state.routes)

    def _observe(self, names):
        return MappingProxyType({name: self._controls.inspect(name) for name in sorted(names)})

    def revalidate(self, current, destination):
        current_routes, next_routes = self._routes(current), self._routes(destination)
        old, new = set(current_routes.values()), set(next_routes.values())
        observed = self._observe(self._controls.approved_names)
        if any(observed[name] != "running" for name in old):
            return False
        if any(observed[name] not in ("created", "exited") for name in new - old):
            return False
        return self._admission(current, destination, observed) is True

    def drain(self, slot):
        if self._traffic.drain(slot) is not True:
            raise BackendUnavailable("Traffic drain was not confirmed.")

    def resume(self, slot):
        if self._traffic.resume(slot) is not True:
            raise BackendUnavailable("Traffic resume was not confirmed.")

    def start(self, name):
        return self._controls.start(name)

    def stop(self, name):
        return self._controls.stop(name)

    def ready(self, name, slot):
        deployment = next((item for item in self._deployments if item.name == name), None)
        if (deployment is None or slot[1] != deployment.task or slot[0] not in deployment.languages
                or self._controls.inspect(name) != "running"):
            return False
        return self._readiness(name, slot) is True

    def verify_reconciliation(self, current, proposed, pending):
        self._routes(current)
        routes = self._routes(proposed)
        referenced = set(routes.values())
        observed = self._observe(self._controls.approved_names)
        if any(observed[name] != "running" for name in referenced):
            return False
        if any(observed[name] not in ("created", "exited") for name in observed.keys() - referenced):
            return False
        if any(not self.ready(name, slot) for slot, name in routes.items()):
            return False
        return self._reconciliation(current, proposed, pending, observed) is True
