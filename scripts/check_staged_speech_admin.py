"""Drive the real Django admin and routed guest views in an isolated fixture."""

import io
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
from time import monotonic, sleep
from urllib.parse import urlsplit
import wave


FIXTURE = Path("/fixture")
DATABASE = FIXTURE / "bookings.sqlite3"
TRAFFIC = FIXTURE / "traffic.sqlite3"
EXPECTED_DRAFTS = FIXTURE / "expected-drafts.json"
SLOTS = (("en", "tts"), ("pl", "tts"), ("en", "stt"), ("pl", "stt"))
ROLES = {f"{task}-{variant}" for _, task in SLOTS for variant in ("old", "new")}
FIXTURE_ENV = {
    "DJANGO_ALLOWED_HOSTS", "DJANGO_DB_PATH", "DJANGO_DEBUG", "DJANGO_SECRET_KEY",
    "HOTEL_SPEECH_CONTROL_HOST", "HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED", "HOTEL_SPEECH_CONTROL_SWITCH_ENABLED",
    "HOTEL_SPEECH_CONTROL_TOKEN", "HOTEL_SPEECH_CONTROL_URL", "HOTEL_SPEECH_ROUTE_TARGETS",
    "HOTEL_SPEECH_TRAFFIC_DB", "HOTEL_STAGE_MODE", "HOTEL_STAGE_WORKERS", "HOTEL_STAGE_RESTART",
    "HOTEL_STAGE_REPLACEMENT",
}


class FixtureFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise FixtureFailure(message)


def private_file(path):
    info = path.lstat()
    return (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and info.st_uid == os.getuid())


def fixture_configuration(*, restarting, replacing):
    env = os.environ
    mode = env.get("HOTEL_STAGE_MODE")
    if mode == "arm64-server":
        source = Path("/stage-src")
        require(sys.platform == "linux" and platform.machine() == "aarch64" and os.getuid() != 0
                and Path.cwd() == source and Path(__file__).resolve().parents[1] == source
                and os.statvfs(source).f_flag & os.ST_RDONLY
                and "GITHUB_ACTIONS" not in env and "RUNNER_ENVIRONMENT" not in env,
                "ARM64 staging requires a read-only candidate mount and an ordinary user.")
        expected = (source / "requirements.txt").read_bytes().replace(b"\r\n", b"\n")
        actual = Path("/app/requirements.txt").read_bytes().replace(b"\r\n", b"\n")
        require(actual == expected, "Cached Django dependencies do not match the candidate requirements.")
    else:
        require(mode is None and sys.platform == "linux" and env.get("GITHUB_ACTIONS") == "true"
                and env.get("RUNNER_ENVIRONMENT") == "github-hosted", "Hosted Linux CI is required.")
    require(not {key for key in env if key.startswith(("DJANGO_", "HOTEL_"))} - FIXTURE_ENV,
            "Unexpected Django or hotel environment settings.")
    dispatch = "0" if replacing else "1"
    require(env.get("DJANGO_DB_PATH") == str(DATABASE) and env.get("HOTEL_SPEECH_TRAFFIC_DB") == str(TRAFFIC)
            and env.get("HOTEL_SPEECH_CONTROL_HOST") == "runtime-ci"
            and env.get("HOTEL_SPEECH_CONTROL_SWITCH_ENABLED") == dispatch
            and env.get("HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED") == dispatch
            and env.get("DJANGO_ALLOWED_HOSTS") == "testserver" and len(env.get("DJANGO_SECRET_KEY", "")) >= 32,
            "Invalid isolated Django configuration.")
    require(not Path("/var/run/docker.sock").exists() and not env.get("DOCKER_HOST"),
            "Django must have no Docker control connection.")
    root = FIXTURE.lstat()
    require(stat.S_ISDIR(root.st_mode) and stat.S_IMODE(root.st_mode) == 0o700 and root.st_uid == os.getuid()
            and private_file(TRAFFIC), "Fixture files must be private.")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from speech_controller.docker_control import private_ipv4
    from speech_controller.readiness import unique_keys
    from speech_controller.routing import load_targets

    workers = json.loads(env["HOTEL_STAGE_WORKERS"], object_pairs_hook=unique_keys)
    require(isinstance(workers, dict) and set(workers) == ROLES, "Four fixture worker roles are required.")
    prefixes = set()
    for role, name in workers.items():
        match = re.fullmatch(r"(hotel-ai-stage-[0-9a-f]{32})-" + role, name) if isinstance(name, str) else None
        require(match is not None, "Invalid disposable worker name.")
        prefixes.add(match[1])
    require(len(prefixes) == 1, "Workers must belong to one isolated run.")
    targets = {item.name: item for item in load_targets(env["HOTEL_SPEECH_ROUTE_TARGETS"])}
    require(set(targets) == set(workers.values()), "Route catalog must match the fixture workers.")
    for role, name in workers.items():
        task = role.split("-")[0]
        target = targets[name]
        address = urlsplit(target.base_url).hostname
        port = 5000 if task == "tts" else 5001
        require(target.task == task and set(target.languages) == {"en", "pl"} and private_ipv4(address)
                and target.base_url == f"http://{address}:{port}", "Invalid fixture model endpoint.")

    if restarting:
        require(private_file(DATABASE) and private_file(EXPECTED_DRAFTS),
                "Restart requires the original private database and expected drafts.")
    else:
        # Exclusively reserve a fresh database before Django can open it.
        descriptor = os.open(DATABASE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    return workers


def admin_url(suffix, **kwargs):
    from django.urls import reverse

    return reverse("admin:model_providers_speechselection_" + suffix, kwargs=kwargs)


def csrf(client):
    return {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}


def live_state(client, revision, routes, *, blocked=False):
    response = client.get(admin_url("controller_status"))
    require(response.status_code == 200 and response.context is not None, "Admin status view failed.")
    context = response.context
    require(context.get("status_error") is None and context["controller_blocked"] is blocked
            and not context["route_mismatch"] and context["journal_revision"] == revision
            and context["observed_revision"] == revision, "Controller revisions or block state disagree.")
    expected = {("English" if language == "en" else "Polish", task.upper()): value
                for (language, task), value in routes.items()}
    actual = {(row["language"], row["task"]): row["observed"] for row in context["status_rows"]}
    require(actual == expected, "A live route differs from the expected assignment.")
    require(context["pending_requests"] == (["stage-interrupted"] if blocked else [])
            and not context["pending_recoveries"], "Unexpected pending controller operations.")


def replay_unconfirmed(client, detail, body, record):
    from model_providers.control_status import ControlStatusError, fetch_control_status

    if record.outcome is not None or record.attempted_at is None:
        return
    # A transport timeout can leave an in-flight operation. Wait until the same
    # controller responds before retrying the original saved admin record once.
    deadline = monotonic() + 16
    while monotonic() < deadline:
        try:
            fetch_control_status()
        except ControlStatusError as error:
            if error.code != "busy":
                raise
            sleep(0.25)
        else:
            response = client.post(detail, body, **csrf(client))
            require(response.status_code == 200, "Retry of saved admin request failed.")
            record.refresh_from_db()
            return
    raise FixtureFailure("Controller remained busy after an unconfirmed reply.")


def recover(client):
    from model_providers.models import SpeechRecoveryRequest

    url = admin_url("recovery_prepare")
    response = client.get(url)
    require(response.status_code == 200 and response.context.get("form") is not None,
            "Blocked controller did not offer a recovery preview.")
    response = client.post(url, {"preview": response.context["form"].initial["preview"],
                                "reason": "Disposable recovery of an interrupted staging operation", "confirmed": "on"},
                           **csrf(client))
    require(response.status_code == 302, "Recovery preparation did not save an admin request.")
    record = SpeechRecoveryRequest.objects.get()
    detail = admin_url("recovery_detail", recovery_id=record.pk)
    require(response.url == detail and record.attempted_at is None and record.payload["expected_revision"] == 0,
            "Recovery preparation dispatched or changed its request.")
    require(client.get(detail).status_code == 200, "Saved recovery detail unavailable.")
    body = {"confirmed": "on"}
    require(client.post(detail, body).status_code == 403, "Recovery confirmation accepted a missing CSRF token.")
    record.refresh_from_db()
    require(record.attempted_at is None, "A denied confirmation reached the controller.")
    require(client.post(detail, body, **csrf(client)).status_code == 200, "Recovery confirmation failed.")
    record.refresh_from_db()
    replay_unconfirmed(client, detail, body, record)
    require(record.completed and record.outcome["status"] == "reconciled"
            and record.outcome["state"] == record.payload["observed"], "Saved admin recovery did not reconcile.")


def switch(client, slot, revision, target):
    from model_providers.dispatch_generation import current_dispatch_generation
    from model_providers.models import SpeechSwitchRequest

    language, task = slot
    label = "English" if language == "en" else "Polish"
    url = admin_url("switch_prepare")
    response = client.get(url)
    require(response.status_code == 200 and response.context.get("rows"), "Switch review did not show the four slots.")
    rows = response.context["rows"]
    row = next(item for item in rows if item["language"] == label and item["task"] == task.upper())
    require(len(rows) == 4 and row["target"] == target and row["preview"], "Expected switch target unavailable.")
    response = client.post(url, {"preview": row["preview"]}, **csrf(client))
    require(response.status_code == 302, "Switch preparation did not save an admin request.")
    record = SpeechSwitchRequest.objects.get(closed=False, dispatch_generation=current_dispatch_generation())
    detail = admin_url("switch_detail", switch_id=record.pk)
    require(response.url == detail and record.attempted_at is None and record.payload == {
        "expected_revision": revision - 1, "request_id": "admin-" + record.pk.hex, "slot": list(slot), "target": target,
    }, "Saved admin switch differs from the reviewed assignment.")
    require(client.get(detail).status_code == 200, "Saved switch detail unavailable.")
    body = {"action": "send", "confirmed": "on"}
    require(client.post(detail, body, **csrf(client)).status_code == 200, "Switch confirmation failed.")
    record.refresh_from_db()
    replay_unconfirmed(client, detail, body, record)
    result = record.outcome or {}
    # Keep diagnosis specific without dumping the record or fixture secrets.
    require(record.closed and result.get("status") == "succeeded" and result["state"]["revision"] == revision,
            f"Saved admin switch for {language} {task} did not succeed ({result.get('reason', 'unconfirmed')}).")


def create_plan(workers):
    from model_providers.models import SpeechDeployment, SpeechProvider, SpeechSelection

    for task in ("tts", "stt"):
        provider = SpeechProvider.objects.create(name=f"Staging {task}", slug=f"stage-{task}", task=task,
                                                  engine="fixture", languages=["en", "pl"],
                                                  architecture="arm64" if os.environ.get("HOTEL_STAGE_MODE") else "amd64")
        deployment = SpeechDeployment.objects.create(name=f"Staging replacement {task}", slug=f"stage-{task}-new",
            provider=provider, host="runtime-ci", container_name=workers[f"{task}-new"])
        for language in ("en", "pl"):
            SpeechSelection.objects.update_or_create(language=language, task=task, defaults={"deployment": deployment})


def guest_draft(language):
    from django.test import Client
    from reception.models import Conversation

    guest = Client(enforce_csrf_checks=True)
    require(guest.get("/", {"lang": language}).status_code == 200, "Guest reception did not load.")
    response = guest.post("/reception/message/", {"language": language, "start": True},
                          content_type="application/json", **csrf(guest))
    require(response.status_code == 200, "Guest booking session did not start.")
    response = guest.post("/reception/message/",
        {"language": language, "message": "Tomorrow" if language == "en" else "Jutro",
         "revision": response.json()["revision"]}, content_type="application/json", **csrf(guest))
    require(response.status_code == 200 and response.json()["stage"] == "check_out",
            "Fictional arrival date did not advance the draft.")
    return guest, Conversation.objects.get(conversation_id=guest.session.session_key).state


def guest_speech(guest, draft):
    from reception.models import Conversation

    response = guest.post("/reception/audio/", {"revision": draft["revision"]},
                          content_type="application/json", **csrf(guest))
    require(response.status_code == 200 and response.headers["Content-Type"] == "audio/wav", "Real Piper audio failed.")
    with wave.open(io.BytesIO(response.content), "rb") as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22050)
                and audio.getnframes() > 0, "Piper response was not a nonempty mono WAV.")
    response = guest.post("/reception/transcribe/", b"\0" * 32000, content_type="application/octet-stream",
                          HTTP_X_RECEPTION_REVISION=draft["revision"], **csrf(guest))
    require(response.status_code == 200 and response.json() == {"text": ""}, "Real Vosk silence probe failed.")
    require(Conversation.objects.get(conversation_id=guest.session.session_key).state == draft, "The booking draft changed.")


def check_restarted_drafts(operator, workers):
    from django.conf import settings
    from django.test import Client
    from model_providers.models import SpeechRecoveryRequest, SpeechSwitchRequest
    from reception.models import Conversation

    staff = Client(enforce_csrf_checks=True)
    staff.force_login(operator)
    live_state(staff, 4, {slot: workers[f"{slot[1]}-new"] for slot in SLOTS})
    expected = json.loads(EXPECTED_DRAFTS.read_text(encoding="utf-8"))
    require(len(expected) == 2 and {draft["language"] for draft in expected.values()} == {"en", "pl"}
            and dict(Conversation.objects.values_list("conversation_id", "state")) == expected,
            "The original bilingual drafts did not survive the restart.")
    for session_id, draft in expected.items():
        guest = Client(enforce_csrf_checks=True)
        guest.cookies[settings.SESSION_COOKIE_NAME] = session_id
        require(guest.get("/", {"lang": draft["language"]}).status_code == 200,
                "Reception could not reopen the original session.")
        guest_speech(guest, draft)
    recovered = list(SpeechRecoveryRequest.objects.values_list("completed", "outcome__status"))
    switches = list(SpeechSwitchRequest.objects.values_list("closed", "outcome__status", "outcome__state__revision"))
    require(recovered == [(True, "reconciled")] and sorted(switches) == [(True, "succeeded", r) for r in range(1, 5)],
            "Restart changed the staged admin audit history.")
    print("Reopened Django verified live admin status, bilingual Piper/Vosk, drafts and audit history.", flush=True)


def check_restored_dispatch(operator, workers):
    from django.test import Client, override_settings
    from model_providers.models import SpeechDeployment, SpeechRecoveryRequest, SpeechSelection, SpeechSwitchRequest

    staff = Client(enforce_csrf_checks=True)
    staff.force_login(operator)
    require(staff.get("/").status_code == 200, "Could not obtain a fixture CSRF token.")
    # This restored database is disposable. Model requests that were still
    # retryable when the booking backup was captured, retaining their old IDs.
    retries = []
    for model, flag, name, key, payload in (
        (SpeechSwitchRequest, "closed", "switch_detail", "switch_id", {"action": "send", "confirmed": "on"}),
        (SpeechRecoveryRequest, "completed", "recovery_detail", "recovery_id", {"confirmed": "on"}),
    ):
        records = model.objects.order_by("pk")
        if model is SpeechSwitchRequest:
            records = records.filter(payload__slot=["en", "tts"])
        record = records.first()
        request_payload = {**record.payload, "expected_revision": 4}
        changes = {}
        if model is SpeechSwitchRequest:
            request_payload["target"] = workers["tts-old"]
            changes["previous_target"] = workers["tts-new"]
        model.objects.filter(pk=record.pk).update(
            **{flag: False}, **changes, outcome=None, attempted_at=None, payload=request_payload,
        )
        before = list(model.objects.order_by("pk").values())
        url = admin_url(name, **{key: record.pk})
        response = staff.post(url, payload, **csrf(staff))
        require(response.status_code == 403 and list(model.objects.order_by("pk").values()) == before,
                "Disabled dispatch accepted or changed a stale admin request.")
        retries.append((model, before, url, payload))

    slot = ("en", "tts")
    routes = {item: workers[f"{item[1]}-new"] for item in SLOTS}
    selection = SpeechSelection.objects.get(language=slot[0], task=slot[1])
    original = selection.deployment
    standby = SpeechDeployment.objects.create(
        name="Replacement standby TTS", slug="replacement-standby-tts", provider=original.provider,
        host=original.host, container_name=workers["tts-old"],
    )
    selection.deployment = standby
    selection.save(update_fields=["deployment"])
    live_state(staff, 4, routes)

    with override_settings(HOTEL_SPEECH_CONTROL_SWITCH_ENABLED=True, HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED=True,
                           HOTEL_SPEECH_CONTROL_GENERATION="disposable-replacement"):
        for model, before, url, payload in retries:
            response = staff.post(url, payload, **csrf(staff))
            require(response.status_code == 200 and response.context["dispatch_allowed"] is False
                    and list(model.objects.order_by("pk").values()) == before,
                    "A restored admin request was not fenced with dispatch enabled.")
        live_state(staff, 4, routes)
        switch(staff, slot, 5, standby.container_name)
        routes[slot] = standby.container_name
        live_state(staff, 5, routes)
        selection.deployment = original
        selection.save(update_fields=["deployment"])
        switch(staff, slot, 6, original.container_name)
        routes[slot] = original.container_name
        live_state(staff, 6, routes)
    print("Restored retries stayed fenced; fresh English TTS switches succeeded at revisions 5 and 6.", flush=True)


def main():
    restart_value = os.environ.get("HOTEL_STAGE_RESTART")
    require(restart_value in (None, "1"), "Unexpected fixture restart flag.")
    restarting = restart_value == "1"
    replacement_value = os.environ.get("HOTEL_STAGE_REPLACEMENT")
    require(replacement_value is None or (replacement_value == "1" and restarting),
            "Replacement mode requires a restored fixture database.")
    replacing = replacement_value == "1"
    workers = fixture_configuration(restarting=restarting, replacing=replacing)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.test import Client
    from django.test.utils import setup_test_environment
    from bookings.models import Booking
    from model_providers.control_status import configuration
    from model_providers.models import SpeechSelection, SpeechSwitchRequest

    configuration()
    setup_test_environment()
    if restarting:
        migrations = MigrationExecutor(connection)
        require(not migrations.migration_plan(migrations.loader.graph.leaf_nodes()), "Restart found pending migrations.")
        operator = get_user_model().objects.get(username="runtime-fixture-operator")
        check_restarted_drafts(operator, workers)
        if replacing:
            check_restored_dispatch(operator, workers)
        require(not Booking.objects.exists(), "The restart created a booking.")
        return

    call_command("migrate", interactive=False, verbosity=0)
    operator = get_user_model().objects.create_user("runtime-fixture-operator", is_staff=True)
    operator.user_permissions.add(*(Permission.objects.get(content_type__app_label="model_providers", codename=name)
        for name in ("view_speechselection", "view_speechdeployment", "view_speechprovider", "view_speechmodel",
                     "recover_speech_controller", "switch_speech_controller")))
    staff = Client(enforce_csrf_checks=True)
    staff.force_login(operator)
    routes = {slot: workers[f"{slot[1]}-old"] for slot in SLOTS}
    live_state(staff, 0, routes, blocked=True)
    recover(staff)
    live_state(staff, 0, routes)
    print("Private Django admin recovered the staged pending operation at revision 0.", flush=True)

    guests = [guest_draft(language) for language in ("en", "pl")]
    create_plan(workers)
    plan = list(SpeechSelection.objects.values_list("language", "task", "deployment_id"))
    for revision, slot in enumerate(SLOTS, 1):
        target = workers[f"{slot[1]}-new"]
        switch(staff, slot, revision, target)
        routes[slot] = target
        live_state(staff, revision, routes)
        if revision in (1, 3, 4):
            for guest, draft in guests:
                guest_speech(guest, draft)
        print(f"Private Django admin switched {slot[0]} {slot[1]} at revision {revision}.", flush=True)
    require(SpeechSwitchRequest.objects.filter(closed=True).count() == 4
            and plan == list(SpeechSelection.objects.values_list("language", "task", "deployment_id"))
            and not Booking.objects.exists(), "Unexpected audit, planned assignment or booking change.")
    descriptor = os.open(EXPECTED_DRAFTS, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({guest.session.session_key: draft for guest, draft in guests}, stream)
    print("Routed English and Polish Piper/Vosk passed during split routes and after all swaps. Booking drafts preserved.")


if __name__ == "__main__":
    try:
        main()
    except FixtureFailure as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit(f"Isolated Django staging failed ({type(error).__name__}).") from None
