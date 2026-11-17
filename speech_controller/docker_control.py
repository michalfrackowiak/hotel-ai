"""Approved existing-container controls, not admission, readiness or guest routing.

The caller must serialize operations and durably record uncertainty. A timeout may
occur after Docker acted; this module never automatically retries a mutation.
"""
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
import json
import os
import platform
import re
import stat
import subprocess
from time import monotonic
from types import MappingProxyType


IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
DAEMON_ID = re.compile(r"[a-zA-Z0-9:_-]{1,128}\Z")
INFO_FORMAT = '{"id":{{json .ID}},"os_type":{{json .OSType}}}'
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
    '"nano_cpus":{{json .HostConfig.NanoCpus}},"memory_bytes":{{json .HostConfig.Memory}},'
    '"state":{{json .State.Status}},"owner":{{json (index .Config.Labels "hotel-ai.controller-owner")}},'
    '"privileged":{{json .HostConfig.Privileged}}}'
)
ENDPOINT_FORMAT = INSPECT_FORMAT[:-1] + (
    ',"network_mode":{{json .HostConfig.NetworkMode}},"networks":['
    '{{$sep := ""}}{{range .NetworkSettings.Networks}}{{$sep}}'
    '{"id":{{json .NetworkID}},"address":{{json .IPAddress}}}{{$sep = ","}}{{end}}]}'
)
NETWORK_FORMAT = '{"id":{{json .Id}},"driver":{{json .Driver}},"scope":{{json .Scope}}}'


def private_ipv4(value):
    """Accept canonical RFC1918 literals only, independently of Python's is_private."""
    try:
        address = IPv4Address(value)
        return isinstance(value, str) and str(address) == value and any(
            address in IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ApprovedContainer:
    name: str
    container_id: str
    image_id: str
    nano_cpus: int
    memory_bytes: int
    owner: str

    def __post_init__(self):
        if (not isinstance(self.name, str) or not IDENTIFIER.fullmatch(self.name)
                or not isinstance(self.container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", self.container_id)
                or not isinstance(self.image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id)
                or type(self.nano_cpus) is not int or not 0 < self.nano_cpus <= 2**63 - 1
                or type(self.memory_bytes) is not int or not 0 < self.memory_bytes <= 2**63 - 1
                or not isinstance(self.owner, str) or not IDENTIFIER.fullmatch(self.owner)):
            raise ValueError("Invalid approved container.")


class ControlUnavailable(Exception):
    pass


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class DockerControls:
    def __init__(self, docker_socket, expected_daemon_id, containers):
        if (not isinstance(docker_socket, str) or not os.path.isabs(docker_socket)
                or any(ord(char) < 32 for char in docker_socket)
                or not isinstance(expected_daemon_id, str) or not DAEMON_ID.fullmatch(expected_daemon_id)):
            raise ValueError("An explicit local socket and daemon ID are required.")
        containers = tuple(containers)
        if not containers or any(not isinstance(item, ApprovedContainer) for item in containers):
            raise ValueError("Approved immutable containers are required.")
        approved = {item.name: item for item in containers}
        if len(approved) != len(containers) or len({item.container_id for item in containers}) != len(containers):
            raise ValueError("Approved container identities must be unique.")
        self._socket = docker_socket
        self._daemon_id = expected_daemon_id
        self._approved = MappingProxyType(approved)
        self._check_socket()

    def _check_socket(self):
        try:
            if platform.system() != "Linux" or not stat.S_ISSOCK(os.lstat(self._socket).st_mode):
                raise ValueError
        except (OSError, ValueError):
            raise ControlUnavailable("Docker control unavailable; reconcile actual state before retrying.") from None

    def start(self, name):
        return self._change(name, "start")

    @property
    def approved_names(self):
        return frozenset(self._approved)

    @property
    def approvals(self):
        return tuple(self._approved.values())

    @property
    def daemon_id(self):
        return self._daemon_id

    def inspect(self, name):
        """Validate daemon/container approval and return state without mutation."""
        return self._change(name, "inspect")

    def stop(self, name):
        return self._change(name, "stop")

    def verify_endpoint(self, name, network_id, address):
        """Read current approved container and bridge identity without mutation."""
        if (not isinstance(network_id, str) or not re.fullmatch(r"[a-f0-9]{64}", network_id)
                or not private_ipv4(address)):
            raise ControlUnavailable("Invalid approved endpoint binding.")
        return self._change(name, "endpoint", endpoint=(network_id, address))

    def _change(self, name, action, *, endpoint=None):
        if not isinstance(name, str) or name not in self._approved:
            raise ControlUnavailable("Container is not approved for control.")
        approved = self._approved[name]
        try:
            deadline = monotonic() + 10
            self._check_socket()
            environment = os.environ.copy()
            for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_TLS"):
                environment.pop(key, None)

            def run(arguments, *, project=False):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ValueError
                result = subprocess.run(
                    ["docker", "--host", "unix://" + self._socket, *arguments], shell=False, env=environment,
                    stdout=subprocess.PIPE if project else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=remaining, check=True,
                )
                if project:
                    if len(result.stdout) > 4096:
                        raise ValueError
                    return json.loads(result.stdout, object_pairs_hook=unique_keys)

            identity = run(["info", "--format", INFO_FORMAT], project=True)
            if identity != {"id": self._daemon_id, "os_type": "linux"}:
                raise ValueError

            def inspect():
                record = run(["inspect", "--type", "container", "--format", ENDPOINT_FORMAT if endpoint else INSPECT_FORMAT,
                              approved.container_id], project=True)
                expected = dict(id=approved.container_id, name="/" + approved.name, image_id=approved.image_id,
                                nano_cpus=approved.nano_cpus, memory_bytes=approved.memory_bytes, owner=approved.owner)
                extra = {"network_mode", "networks"} if endpoint else set()
                if (not isinstance(record, dict) or set(record) != set(expected) | {"state", "privileged"} | extra
                        or any(record[key] != value for key, value in expected.items())
                        or type(record["nano_cpus"]) is not int or type(record["memory_bytes"]) is not int
                        or record["privileged"] is not False
                        or record["state"] not in ("running", "created", "exited")):
                    raise ValueError
                if endpoint:
                    network_id, address = endpoint
                    networks = record["networks"]
                    if (record["state"] != "running" or not isinstance(record["network_mode"], str)
                            or not IDENTIFIER.fullmatch(record["network_mode"])
                            or record["network_mode"] in ("host", "none")
                            or not isinstance(networks, list) or not 1 <= len(networks) <= 8
                            or any(not isinstance(item, dict) or set(item) != {"id", "address"}
                                   or not isinstance(item["id"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["id"])
                                   or not isinstance(item["address"], str) for item in networks)
                            or len({item["id"] for item in networks}) != len(networks)
                            or {"id": network_id, "address": address} not in networks):
                        raise ValueError
                return record["state"]

            before = inspect()
            if endpoint:
                network_id, _ = endpoint
                network = run(["network", "inspect", "--format", NETWORK_FORMAT, network_id], project=True)
                if network != {"id": network_id, "driver": "bridge", "scope": "local"}:
                    raise ValueError
                return True
            if action == "inspect":
                return before
            if action == "start" and before == "running" or action == "stop" and before in ("created", "exited"):
                return before
            arguments = ["start", approved.container_id] if action == "start" else ["stop", "--time", "3", approved.container_id]
            run(arguments)
            after = inspect()
            if action == "start" and after != "running" or action == "stop" and after != "exited":
                raise ValueError
            return after
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, RecursionError):
            raise ControlUnavailable("Docker control unavailable; reconcile actual state before retrying.") from None
