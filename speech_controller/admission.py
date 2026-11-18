"""Fresh conservative native-host checks; not capacity reservation or topology proof."""
from dataclasses import dataclass
from decimal import Decimal
import os
import re

from .docker_control import DockerControls, ControlUnavailable
from .lifecycle import SLOTS, State
from .resources import host_resources


MIB = 1048576
MAX_VALUE = 2**63 - 1


def positive_integer(value):
    return type(value) is int and 0 < value <= MAX_VALUE


@dataclass(frozen=True)
class ResourcePolicy:
    daemon_id: str
    storage_path: str
    topology_confirmed: bool
    cpu_budget_nanos: int
    memory_budget_bytes: int
    memory_reserve_bytes: int
    disk_reserve_bytes: int
    artifact_bytes: tuple

    def __post_init__(self):
        valid = (isinstance(self.daemon_id, str)
                 and re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", self.daemon_id)
                 and isinstance(self.storage_path, str) and os.path.isabs(self.storage_path)
                 and not any(ord(char) < 32 for char in self.storage_path)
                 and self.topology_confirmed is True
                 and all(positive_integer(value) for value in (
                     self.cpu_budget_nanos, self.memory_budget_bytes,
                     self.memory_reserve_bytes, self.disk_reserve_bytes))
                 and type(self.artifact_bytes) is tuple and self.artifact_bytes)
        if not valid:
            raise ValueError("Explicit immutable native-host resource policy is required.")
        images = set()
        for item in self.artifact_bytes:
            if (type(item) is not tuple or len(item) != 2 or not isinstance(item[0], str)
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", item[0])
                    or not positive_integer(item[1]) or item[0] in images):
                raise ValueError("Distinct approved image IDs and positive artifact budgets are required.")
            images.add(item[0])


class ControllerAdmission:
    """Read metadata and host resources anew on each pre-drain/pre-start check.

    Policy is local operator configuration. The caller must serialize switching;
    Docker observations and host metrics are not one atomic reservation.
    """
    def __init__(self, controls, policy):
        if (not isinstance(controls, DockerControls) or not isinstance(policy, ResourcePolicy)
                or controls.daemon_id != policy.daemon_id or not 1 <= len(controls.approvals) <= 8
                or {item.image_id for item in controls.approvals} - dict(policy.artifact_bytes).keys()):
            raise ValueError("Matching pinned controls and complete resource policy are required.")
        self._controls, self._policy = controls, policy

    @property
    def controls(self):
        return self._controls

    def _routes(self, state):
        if (not isinstance(state, State) or type(state.revision) is not int or state.revision < 0
                or type(state.routes) is not tuple or len(state.routes) != len(SLOTS)):
            raise ValueError
        routes = dict(state.routes)
        if (set(routes) != SLOTS or tuple(sorted(routes.items())) != state.routes
                or any(type(name) is not str or name not in self._controls.approved_names for name in routes.values())):
            raise ValueError
        return set(routes.values())

    def __call__(self, current, destination, observations):
        try:
            old, new = self._routes(current), self._routes(destination)
            if destination.revision != current.revision + 1 or set(observations) != self._controls.approved_names:
                return False
            fresh = {name: self._controls.inspect(name) for name in sorted(self._controls.approved_names)}
            if (dict(observations) != fresh or any(state not in ("created", "exited", "running") for state in fresh.values())
                    or any(fresh[name] != "running" for name in old)
                    or any(fresh[name] not in ("created", "exited") for name in new - old)):
                return False
            overlap = old | new | {name for name, state in fresh.items() if state == "running"}
            return self._capacity(overlap)
        except (OSError, ValueError, TypeError, KeyError, ControlUnavailable):
            return False

    def verify_reconciliation(self, current, proposed, pending, observations):
        """Fresh capacity and worker checks after the backend's bound probes."""
        try:
            self._routes(current)
        except (ValueError, TypeError):
            return False
        return self._verify_selected(proposed, observations, allow_stopped_selected=False)

    def verify_boot(self, state, observations):
        """Require capacity for the full selected set, including stopped workers."""
        return self._verify_selected(state, observations, allow_stopped_selected=True)

    def _verify_selected(self, state, observations, *, allow_stopped_selected):
        try:
            selected = self._routes(state)
            if set(observations) != self._controls.approved_names:
                return False
            fresh = {name: self._controls.inspect(name) for name in sorted(self._controls.approved_names)}
            stopped = ("created", "exited")
            selected_states = ("running", *stopped) if allow_stopped_selected else ("running",)
            if (dict(observations) != fresh
                    or any(fresh[name] not in selected_states for name in selected)
                    or any(fresh[name] not in stopped for name in fresh.keys() - selected)):
                return False
            return self._capacity(selected)
        except (OSError, ValueError, TypeError, KeyError, ControlUnavailable):
            return False

    def _capacity(self, names):
        approved = [item for item in self._controls.approvals if item.name in names]
        cpu = sum(item.nano_cpus for item in approved)
        memory = sum(item.memory_bytes for item in approved)
        artifacts = dict(self._policy.artifact_bytes)
        disk = self._policy.disk_reserve_bytes + sum(artifacts[image] for image in {item.image_id for item in approved})
        resources = host_resources(self._policy.storage_path)
        if (not isinstance(resources, dict) or resources.get("scope") != "controller_host"
                or resources.get("platform") != "linux"):
            return False
        count = resources.get("cpu_count")
        if not positive_integer(count):
            return False
        values = {}
        for key in ("memory_total_mib", "memory_available_mib", "disk_total_mib", "disk_free_mib"):
            value = resources.get(key)
            if type(value) not in (int, float):
                return False
            value = Decimal(str(value))
            if not value.is_finite() or not 0 <= value <= MAX_VALUE:
                return False
            values[key] = value * MIB
        if (not 0 < values["memory_total_mib"] or not 0 < values["disk_total_mib"]
                or values["memory_available_mib"] > values["memory_total_mib"]
                or values["disk_free_mib"] > values["disk_total_mib"]):
            return False
        return (cpu <= self._policy.cpu_budget_nanos and cpu <= count * 1000000000
                and memory <= self._policy.memory_budget_bytes
                and memory + self._policy.memory_reserve_bytes <= values["memory_available_mib"]
                and disk <= values["disk_free_mib"])
