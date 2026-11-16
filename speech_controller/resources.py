"""Best-effort Linux controller-host observations, never admission decisions."""
import os
from pathlib import Path
import platform
import re
import shutil


def host_resources(storage_path=""):
    result = dict(scope="controller_host", platform="unsupported", cpu_count=None,
                  memory_total_mib=None, memory_available_mib=None,
                  disk_total_mib=None, disk_free_mib=None)
    if platform.system() != "Linux":
        return result
    result["platform"] = "linux"
    count = os.cpu_count()
    if type(count) is int and count > 0:
        result["cpu_count"] = count
    try:
        with Path("/proc/meminfo").open(encoding="ascii") as source:
            text = source.read(65537)
        if len(text) > 65536:
            raise ValueError
        values = {}
        for name in ("MemTotal", "MemAvailable"):
            matches = re.findall(rf"^{name}:\s+([0-9]+) kB\s*$", text, re.MULTILINE)
            if len(matches) != 1:
                raise ValueError
            values[name] = int(matches[0])
        total, available = values["MemTotal"], values["MemAvailable"]
        if not 0 <= available <= total <= 2**63 - 1 or total == 0:
            raise ValueError
        result.update(memory_total_mib=total / 1024, memory_available_mib=available / 1024)
    except (OSError, UnicodeError, ValueError):
        pass
    if storage_path:
        try:
            usage = shutil.disk_usage(storage_path)
            if (type(usage.total) is not int or type(usage.free) is not int
                    or not 0 <= usage.free <= usage.total <= 2**63 - 1 or usage.total == 0):
                raise ValueError
            result.update(disk_total_mib=usage.total / 1048576, disk_free_mib=usage.free / 1048576)
        except (OSError, ValueError):
            pass
    return result
