"""Strict private local startup manifest; no credentials or state initialization."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import stat

from .admission import ResourcePolicy
from .dispatch import ControlConfiguration
from .docker_control import ApprovedContainer
from .endpoint_binding import EndpointBinding
from .readiness import ProbeTarget, unique_keys


def private_file(value):
    """Reject aliases and nonprivate files before any SQLite open can create state."""
    if type(value) is not str or not value or any(ord(c) < 32 for c in value):
        raise ValueError
    path = Path(value)
    if not path.is_absolute() or str(path.resolve(strict=True)) != value:
        raise ValueError
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError
    info, parent = path.stat(), path.parent.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
        raise ValueError
    return path, (info.st_dev, info.st_ino)


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError


@dataclass(frozen=True)
class RuntimeConfiguration:
    control: ControlConfiguration
    approvals: tuple
    targets: tuple
    bindings: tuple
    policy: ResourcePolicy
    traffic_path: str
    journal_path: str
    docker_socket: str
    daemon_id: str
    identities: tuple

    @classmethod
    def load(cls, path, token):
        try:
            if platform.system() != "Linux":
                raise ValueError
            manifest, identity = private_file(path)
            descriptor = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (info.st_dev, info.st_ino) != identity or info.st_size > 65536:
                    raise ValueError
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(raw, object_pairs_hook=unique_keys)
            fields(value, ("version", "actor", "port", "docker_socket", "daemon_id", "traffic_path",
                           "journal_path", "policy", "deployments"))
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            control = ControlConfiguration(token, value["actor"], value["port"])
            traffic, traffic_id = private_file(value["traffic_path"])
            journal, journal_id = private_file(value["journal_path"])
            if len({identity, traffic_id, journal_id}) != 3:
                raise ValueError
            policy = value["policy"]
            fields(policy, ("storage_path", "topology_confirmed", "cpu_budget_nanos", "memory_budget_bytes",
                            "memory_reserve_bytes", "disk_reserve_bytes", "artifact_bytes"))
            artifacts = policy["artifact_bytes"]
            if type(artifacts) is not list or not 1 <= len(artifacts) <= 8 or any(
                    type(item) is not list or len(item) != 2 for item in artifacts):
                raise ValueError
            policy = ResourcePolicy(**(policy | {"artifact_bytes": tuple(map(tuple, artifacts)),
                                                "daemon_id": value["daemon_id"]}))
            deployments = value["deployments"]
            if type(deployments) is not list or not 1 <= len(deployments) <= 8:
                raise ValueError
            approvals, targets, bindings = [], [], []
            approval_fields = ("name", "container_id", "image_id", "nano_cpus", "memory_bytes", "owner")
            for item in deployments:
                fields(item, (*approval_fields, "task", "languages", "base_url", "network_id", "address"))
                if type(item["languages"]) is not list:
                    raise ValueError
                approvals.append(ApprovedContainer(**{key: item[key] for key in approval_fields}))
                targets.append(ProbeTarget(item["name"], item["task"], item["base_url"], tuple(item["languages"])))
                bindings.append(EndpointBinding(item["name"], item["network_id"], item["address"]))
            socket = value["docker_socket"]
            if (type(socket) is not str or not Path(socket).is_absolute()
                    or str(Path(socket).resolve(strict=True)) != socket
                    or any(part.is_symlink() for part in (Path(socket), *Path(socket).parents))
                    or not stat.S_ISSOCK(Path(socket).stat().st_mode)):
                raise ValueError
            return cls(control, tuple(approvals), tuple(targets), tuple(bindings), policy,
                       str(traffic), str(journal), socket, value["daemon_id"], (traffic_id, journal_id))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise ValueError("Invalid private controller configuration.") from None
