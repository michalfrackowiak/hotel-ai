"""Conservative resource preview; operator assertions never authorize execution."""
import json
import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug

from .models import PINNED_IMAGE
from .preflight import unique_keys


def admission_policies(raw):
    policies = json.loads(raw, object_pairs_hook=unique_keys)
    if not isinstance(policies, dict):
        raise ValueError
    for host, policy in policies.items():
        validate_slug(host)
        if not host or not isinstance(policy, dict) or set(policy) != {
            "daemon_id", "topology_confirmed", "memory_reserve_mib", "disk_reserve_mib", "artifact_requirements_mib"
        }:
            raise ValueError
        if (not isinstance(policy["daemon_id"], str)
                or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", policy["daemon_id"])
                or policy["topology_confirmed"] is not True):
            raise ValueError
        for key in ("memory_reserve_mib", "disk_reserve_mib"):
            if type(policy[key]) is not int or policy[key] <= 0:
                raise ValueError
        artifacts = policy["artifact_requirements_mib"]
        if not isinstance(artifacts, dict):
            raise ValueError
        for image, size in artifacts.items():
            PINNED_IMAGE(image)
            if type(size) is not int or size <= 0:
                raise ValueError
    return policies


def evaluate_admission(snapshot, planned, overlap_memory, overlap_cpu):
    result = {"passed": False, "issues": []}
    try:
        policies = admission_policies(settings.HOTEL_SPEECH_ADMISSION_POLICIES)
    except (ValueError, TypeError, ValidationError, RecursionError):
        result["issues"].append("Invalid resource admission policy. Configure the daemon, confirmed topology, positive reserves and pinned artifact estimates.")
        return result
    policy = policies.get(snapshot["host"])
    if policy is None:
        result["issues"].append("No local resource admission policy is configured for this host.")
        return result
    identity = snapshot.get("daemon_identity", {})
    if (identity.get("connection") != "pinned_local_socket" or identity.get("os_type") != "linux"
            or identity.get("id") != policy["daemon_id"]):
        result["issues"].append("A pinned Linux daemon matching the local policy is required.")
    resources = snapshot.get("host_resources", {})
    if (resources.get("scope") != "controller_host" or resources.get("platform") != "linux"
            or any(resources.get(key) is None for key in ("cpu_count", "memory_available_mib", "disk_free_mib"))):
        result["issues"].append("Known Linux CPU, available memory and free storage observations are required.")
    images = {deployment.provider.image for deployment in planned}
    if any(image not in policy["artifact_requirements_mib"] for image in images):
        result["issues"].append("Every planned image needs a local peak artifact-storage estimate.")
    if result["issues"]:
        return result
    # Reserve full limits even for already active workers: their growth is unmeasured.
    memory_required = overlap_memory + Decimal(policy["memory_reserve_mib"])
    artifact_required = sum(Decimal(policy["artifact_requirements_mib"][image]) for image in images)
    disk_required = artifact_required + Decimal(policy["disk_reserve_mib"])
    memory_available = Decimal(str(resources["memory_available_mib"]))
    disk_free = Decimal(str(resources["disk_free_mib"]))
    result.update(memory_required=memory_required, memory_available=memory_available,
                  disk_required=disk_required, disk_free=disk_free,
                  artifact_required=artifact_required, image_count=len(images),
                  cpu_required=overlap_cpu, cpu_count=resources["cpu_count"])
    if overlap_cpu > resources["cpu_count"]:
        result["issues"].append("Configured overlap CPU exceeds the observed logical CPU count.")
    if memory_required > memory_available:
        result["issues"].append("Available memory cannot cover full overlap limits plus the memory reserve.")
    if disk_required > disk_free:
        result["issues"].append("Free storage cannot cover peak artifact estimates plus the disk reserve.")
    result["passed"] = not result["issues"]
    return result
