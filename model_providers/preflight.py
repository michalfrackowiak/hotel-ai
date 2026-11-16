"""Read-only catalog checks; never authorizes or performs live activation."""
import json
from decimal import Decimal, DecimalException

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug

from .models import PINNED_IMAGE, SpeechSelection


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def host_policies(raw):
    policies = json.loads(raw, parse_float=Decimal, object_pairs_hook=unique_keys)
    if not isinstance(policies, dict):
        raise ValueError
    for host, policy in policies.items():
        validate_slug(host)
        if not host or not isinstance(policy, dict) or set(policy) != {
            "architecture", "cpu_limit", "memory_mib", "approved_images"
        }:
            raise ValueError
        cpu, memory = policy["cpu_limit"], policy["memory_mib"]
        if (policy["architecture"] not in ("arm64", "amd64")
                or type(cpu) not in (int, Decimal) or not Decimal(cpu).is_finite() or cpu <= 0
                or type(memory) is not int or memory <= 0
                or not isinstance(policy["approved_images"], list)):
            raise ValueError
        for image in policy["approved_images"]:
            if not isinstance(image, str) or not image:
                raise ValueError
            PINNED_IMAGE(image)
    return policies


def check_speech_plan():
    issues, totals = [], {}
    try:
        policies = host_policies(settings.HOTEL_SPEECH_HOST_POLICIES)
    except (ValueError, TypeError, DecimalException, ValidationError):
        policies = {}
        issues.append("Invalid HOTEL_SPEECH_HOST_POLICIES: configure host labels, architecture, positive CPU/RAM budgets and pinned approved_images.")
    slots = list(SpeechSelection.objects.select_related("deployment__provider"))
    expected = {(language, task) for language in ("en", "pl") for task in ("stt", "tts")}
    if {(slot.language, slot.task) for slot in slots} != expected or len(slots) != 4:
        issues.append("The plan must contain exactly the four English/Polish STT/TTS slots.")
    deployments = {}
    for slot in slots:
        if not slot.deployment_id:
            issues.append(f"{slot}: select a planned deployment.")
            continue
        deployment = slot.deployment
        deployments.setdefault(deployment.pk, (deployment, set()))[1].add(slot.language)
        provider = deployment.provider
        if (slot.task != provider.task or not isinstance(provider.languages, list)
                or slot.language not in provider.languages):
            issues.append(f"{slot}: deployment does not match the required task and language.")
    for deployment, languages in deployments.values():
        provider = deployment.provider
        try:
            deployment.full_clean()
            provider.full_clean()
        except ValidationError:
            issues.append(f"{deployment}: correct invalid deployment/provider catalog fields.")
            continue
        policy = policies.get(deployment.host)
        total = totals.setdefault(deployment.host, {
            "host": deployment.host, "deployments": 0, "cpu": Decimal(0), "memory_mib": 0,
            "cpu_budget": policy["cpu_limit"] if policy else None,
            "memory_budget": policy["memory_mib"] if policy else None,
        })
        total["deployments"] += 1
        total["cpu"] += provider.cpu_limit
        total["memory_mib"] += provider.memory_mib
        if not policy:
            issues.append(f"{deployment}: host has no locally approved policy.")
        else:
            if not provider.image or provider.image not in policy["approved_images"]:
                issues.append(f"{deployment}: pinned image is not approved for this host.")
            if provider.architecture not in (policy["architecture"], "multi"):
                issues.append(f"{deployment}: architecture does not match this host.")
        if not provider.license.strip():
            issues.append(f"{deployment}: record the engine license.")
        models = list(provider.speech_models.all())
        for language in sorted(languages):
            complete = False
            for model in models:
                if model.language != language or not model.checksum or not model.license.strip():
                    continue
                try:
                    model.full_clean()
                    complete = True
                except ValidationError:
                    pass
            if not complete:
                issues.append(f"{deployment}: {language} needs a model/voice with valid checksum and license metadata.")
    for host, total in totals.items():
        if total["cpu_budget"] is not None and total["cpu"] > total["cpu_budget"]:
            issues.append(f"{host}: planned CPU exceeds the reserved speech budget.")
        if total["memory_budget"] is not None and total["memory_mib"] > total["memory_budget"]:
            issues.append(f"{host}: planned RAM exceeds the reserved speech budget.")
    return {"issues": issues, "totals": list(totals.values()), "passed": not issues}
