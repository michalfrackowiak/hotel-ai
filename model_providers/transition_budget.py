"""Conservative configured overlap preview, never execution admission."""
from decimal import Decimal
from django.db import transaction

from .controller_client import InventoryError, fetch_inventory
from .models import SpeechDeployment
from .preflight import check_speech_plan


ACTIVE_STATES = {"running", "paused", "restarting", "removing"}


def transition_budget_report():
    # Capture catalog inputs together; never hold a database transaction over HTTP.
    with transaction.atomic():
        preflight = check_speech_plan()
        planned = list(SpeechDeployment.objects.filter(planned_selections__isnull=False)
                       .select_related("provider").distinct()) if preflight["passed"] else []
    result = {"issues": list(preflight["issues"]), "rows": [], "passed": False}
    # Invalid catalog values cannot safely participate in resource arithmetic.
    if not preflight["passed"]:
        return result
    try:
        snapshot = fetch_inventory()
    except InventoryError as error:
        result["issues"].append(str(error))
        return result
    host = snapshot["host"]
    result.update(observed_host=host, observed_at=snapshot["observed_at"])
    if any(deployment.host != host for deployment in planned):
        result["issues"].append("All planned hosts must be covered by this single controller snapshot.")
        return result
    budget = next((total for total in preflight["totals"] if total["host"] == host), None)
    if budget is None:
        result["issues"].append("No approved host budget is available for this snapshot.")
        return result
    observed = {item["container_name"]: item for item in snapshot["deployments"]}
    active_cpu = active_memory = overlap_cpu = overlap_memory = Decimal(0)
    complete = True
    active_count = 0
    for item in observed.values():
        if item["state"] not in ACTIVE_STATES:
            continue
        active_count += 1
        result["rows"].append({"name": item["container_name"], "action": "Retain observed active container"})
        if item["cpu_limit"] is None or item["memory_mib"] is None:
            complete = False
            result["issues"].append(f"{item['container_name']}: active container has unknown configured limits.")
            continue
        active_cpu += Decimal(str(item["cpu_limit"]))
        active_memory += Decimal(str(item["memory_mib"]))
    overlap_cpu, overlap_memory = active_cpu, active_memory
    for deployment in planned:
        provider = deployment.provider
        item = observed.get(deployment.container_name)
        if item is not None:
            matches = (item["configured_image"] == provider.image
                       and item["cpu_limit"] is not None and item["memory_mib"] is not None
                       and Decimal(str(item["cpu_limit"])) == provider.cpu_limit
                       and Decimal(str(item["memory_mib"])) == provider.memory_mib)
            if not matches:
                complete = False
                result["issues"].append(f"{deployment.container_name}: existing name conflicts with planned image or limits.")
                result["rows"].append({"name": deployment.container_name, "action": "Blocked name conflict"})
                continue
            if item["state"] in ACTIVE_STATES:
                result["rows"].append({"name": deployment.container_name, "action": "Reuse matching active container; counted once"})
                continue
        overlap_cpu += provider.cpu_limit
        overlap_memory += provider.memory_mib
        result["rows"].append({"name": deployment.container_name,
                               "action": "Add planned limits (unobserved)" if item is None else "Add planned limits (currently inactive)"})
    result.update(active_count=active_count, planned_count=len(planned),
                  active_cpu=active_cpu if complete else None,
                  active_memory=active_memory if complete else None,
                  overlap_cpu=overlap_cpu if complete else None,
                  overlap_memory=overlap_memory if complete else None,
                  cpu_budget=budget["cpu_budget"], memory_budget=budget["memory_budget"])
    if complete:
        if overlap_cpu > budget["cpu_budget"]:
            result["issues"].append("Configured overlap CPU exceeds the reserved speech budget.")
        if overlap_memory > budget["memory_budget"]:
            result["issues"].append("Configured overlap RAM exceeds the reserved speech budget.")
    result["passed"] = not result["issues"]
    return result
