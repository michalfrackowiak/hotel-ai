"""Native Linux bridge endpoint identity around bounded model readiness probes."""
from dataclasses import dataclass
import re
from types import MappingProxyType
from urllib.parse import urlsplit

from .docker_control import ControlUnavailable, DockerControls, private_ipv4
from .lifecycle import identifier
from .readiness import ProbeTarget, ReadinessProbes


@dataclass(frozen=True)
class EndpointBinding:
    name: str
    network_id: str
    address: str

    def __post_init__(self):
        if (not identifier(self.name) or not isinstance(self.network_id, str)
                or not re.fullmatch(r"[a-f0-9]{64}", self.network_id) or not private_ipv4(self.address)):
            raise ValueError("An approved container, full network ID and private IPv4 address are required.")


class BoundReadiness:
    """Require fresh Docker binding checks both before and after actual inference."""
    def __init__(self, controls, targets, bindings):
        targets, bindings = tuple(targets), tuple(bindings)
        if (not isinstance(controls, DockerControls) or not 1 <= len(targets) <= 8
                or any(not isinstance(target, ProbeTarget) for target in targets)
                or any(not isinstance(binding, EndpointBinding) for binding in bindings)
                or len(targets) != len(bindings)
                or len({target.name for target in targets}) != len(targets)
                or len({binding.name for binding in bindings}) != len(bindings)
                or {target.name for target in targets} != controls.approved_names
                or {binding.name for binding in bindings} != controls.approved_names):
            raise ValueError("Matching complete controls, targets and endpoint bindings are required.")
        by_name = {binding.name: binding for binding in bindings}
        endpoints, networks = set(), {}
        for target in targets:
            binding = by_name[target.name]
            port = urlsplit(target.base_url).port
            if (port is None or target.base_url != f"http://{binding.address}:{port}"
                    or (binding.address, port) in endpoints
                    or binding.address in networks and networks[binding.address] != binding.network_id):
                raise ValueError("Distinct canonical direct bridge HTTP endpoints are required.")
            endpoints.add((binding.address, port))
            networks[binding.address] = binding.network_id
        self._controls = controls
        self._targets = MappingProxyType({target.name: target for target in targets})
        self._bindings = MappingProxyType(by_name)
        self._probes = ReadinessProbes(targets)

    @property
    def controls(self):
        return self._controls

    @property
    def targets(self):
        return tuple(self._targets.values())

    def __call__(self, name, slot):
        if (not isinstance(name, str) or type(slot) is not tuple or len(slot) != 2
                or any(type(value) is not str for value in slot)):
            return False
        target = self._targets.get(name)
        if target is None or slot[1] != target.task or slot[0] not in target.languages:
            return False
        binding = self._bindings[name]
        try:
            return (self._controls.verify_endpoint(name, binding.network_id, binding.address) is True
                    and self._probes(name, slot) is True
                    and self._controls.verify_endpoint(name, binding.network_id, binding.address) is True)
        except ControlUnavailable:
            return False
