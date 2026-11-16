"""Read-only private controller client. Observations never activate a plan."""
from datetime import datetime, timezone
from decimal import Decimal
from http.client import HTTPException
import json
import math
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings

from .models import SpeechDeployment


class InventoryError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def controller_configuration():
    url = settings.HOTEL_SPEECH_CONTROLLER_URL
    if not url:
        raise InventoryError("Controller inventory is not configured.")
    token, host = settings.HOTEL_SPEECH_CONTROLLER_TOKEN, settings.HOTEL_SPEECH_CONTROLLER_HOST
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "::1")
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path not in ("", "/")
                or parts.port is not None and not 1 <= parts.port <= 65535
                or any(ord(char) <= 32 for char in url)
                or not 32 <= len(token) <= 256 or not token.isascii()
                or any(not 33 <= ord(char) <= 126 for char in token)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", host)):
            raise ValueError
    except (ValueError, TypeError):
        raise InventoryError("Controller configuration is invalid.") from None
    return url.rstrip("/") + "/v1/inventory", token, host


def validate_inventory(data, host):
    if not isinstance(data, dict) or set(data) != {"host", "observed_at", "deployments"} or data["host"] != host:
        raise ValueError
    observed = datetime.fromisoformat(data["observed_at"])
    if observed.tzinfo is None or not -5 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 30:
        raise ValueError
    records = data["deployments"]
    if not isinstance(records, list) or not 1 <= len(records) <= 8:
        raise ValueError
    names = set()
    expected = {"container_name", "id", "state", "health", "image_id", "configured_image", "cpu_limit", "memory_mib"}
    for item in records:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError
        for key in expected - {"cpu_limit", "memory_mib"}:
            if not isinstance(item[key], str) or not 1 <= len(item[key]) <= 512:
                raise ValueError
        name = item["container_name"]
        if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name) or name in names
                or not re.fullmatch(r"[a-f0-9]{64}", item["id"])
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", item["image_id"])
                or item["state"] not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
                or item["health"] not in {"none", "starting", "healthy", "unhealthy"}):
            raise ValueError
        names.add(name)
        for key in ("cpu_limit", "memory_mib"):
            value = item[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError
    return data


def fetch_inventory():
    url, token, host = controller_configuration()
    request = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        opener = build_opener(ProxyHandler({}), NoRedirects())
        with opener.open(request, timeout=6) as response:
            if response.status != 200:
                raise ValueError
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError
        return validate_inventory(json.loads(raw, object_pairs_hook=unique_keys), host)
    except (OSError, URLError, HTTPException, ValueError, TypeError, OverflowError, RecursionError):
        raise InventoryError("Controller inventory is unavailable or invalid. Check private connectivity and configuration.") from None


def inventory_report():
    try:
        snapshot = fetch_inventory()
    except InventoryError as error:
        return {"inventory_error": str(error), "rows": []}
    planned = {item.container_name: item for item in SpeechDeployment.objects.filter(
        host=snapshot["host"]).select_related("provider")}
    rows = []
    for observed in snapshot["deployments"]:
        deployment = planned.pop(observed["container_name"], None)
        rows.append({"observed": observed, "planned": deployment,
            "image_matches": bool(deployment and deployment.provider.image
                                  and deployment.provider.image == observed["configured_image"]),
            "limits_match": bool(deployment and observed["cpu_limit"] is not None
                                 and observed["memory_mib"] is not None
                                 and deployment.provider.cpu_limit == Decimal(str(observed["cpu_limit"]))
                                 and deployment.provider.memory_mib == Decimal(str(observed["memory_mib"]))),
        })
    rows.extend({"planned": item, "observed": None} for item in planned.values())
    return {"rows": rows, "observed_at": snapshot["observed_at"], "observed_host": snapshot["host"]}
