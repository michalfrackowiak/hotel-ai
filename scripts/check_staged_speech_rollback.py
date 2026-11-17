"""Verify fixed speech endpoints and existing drafts in a restored fixture."""

import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

from check_staged_speech_admin import FixtureFailure, guest_speech, require


FIXTURE = Path("/fixture")
RESTORED = FIXTURE / "restored.sqlite3"
ENDPOINTS = {"HOTEL_PIPER_URL": 5000, "HOTEL_ASR_URL": 5001}
FIXTURE_ENV = {"DJANGO_ALLOWED_HOSTS", "DJANGO_DB_PATH", "DJANGO_SECRET_KEY", *ENDPOINTS}


def fixture_configuration():
    env = os.environ
    require(sys.platform == "linux" and env.get("GITHUB_ACTIONS") == "true"
            and env.get("RUNNER_ENVIRONMENT") == "github-hosted", "Hosted Linux CI is required.")
    require({key for key in env if key.startswith(("DJANGO_", "HOTEL_"))} == FIXTURE_ENV
            and env["DJANGO_DB_PATH"] == str(RESTORED) and env["DJANGO_ALLOWED_HOSTS"] == "testserver"
            and bool(re.fullmatch(r"[a-f0-9]{64}", env["DJANGO_SECRET_KEY"])), "Invalid fixed speech fixture settings.")
    require(not Path("/var/run/docker.sock").exists() and not env.get("DOCKER_HOST"),
            "The rollback verifier must have no Docker control connection.")
    root, database = FIXTURE.lstat(), RESTORED.lstat()
    require(stat.S_ISDIR(root.st_mode) and stat.S_IMODE(root.st_mode) == 0o700 and root.st_uid == os.getuid()
            and stat.S_ISREG(database.st_mode) and stat.S_IMODE(database.st_mode) == 0o600
            and database.st_nlink == 1 and database.st_uid == os.getuid()
            and set(FIXTURE.iterdir()) == {RESTORED}, "Only the private restored database may be mounted.")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from speech_controller.docker_control import private_ipv4

    addresses = set()
    for setting, port in ENDPOINTS.items():
        url = env[setting]
        address = urlsplit(url).hostname
        require(private_ipv4(address) and url == f"http://{address}:{port}", "Invalid fixed worker endpoint.")
        addresses.add(address)
    require(len(addresses) == len(ENDPOINTS), "Expected separate fixed speech workers.")


def main():
    fixture_configuration()
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.conf import settings
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.test import Client

    from bookings.models import Booking
    from model_providers.models import SpeechRecoveryRequest, SpeechSwitchRequest
    from reception.models import Conversation

    require(not (settings.HOTEL_SPEECH_TRAFFIC_DB or settings.HOTEL_SPEECH_ROUTE_TARGETS
                 or settings.HOTEL_SPEECH_CONTROL_URL or settings.HOTEL_SPEECH_CONTROL_TOKEN
                 or settings.HOTEL_SPEECH_CONTROL_SWITCH_ENABLED or settings.HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED),
            "Fixed speech rollback must run with controller and routing settings disabled.")
    migrations = MigrationExecutor(connection)
    require(not migrations.migration_plan(migrations.loader.graph.leaf_nodes()),
            "The restored fixture must already match the candidate migration state.")
    drafts = list(Conversation.objects.all())
    require(len(drafts) == 2 and {draft.state["language"] for draft in drafts} == {"en", "pl"},
            "The restore did not contain both language drafts.")
    for draft in drafts:
        require(draft.state["stage"] == "check_out" and SessionStore(session_key=draft.session_id).load().get("reception"),
                "An existing restored booking session was not readable.")
        guest = Client(enforce_csrf_checks=True)
        guest.cookies[settings.SESSION_COOKIE_NAME] = draft.session_id
        require(guest.get("/", {"lang": draft.state["language"]}).status_code == 200,
                "Fixed speech reception did not load for the restored session.")
        guest_speech(guest, draft.state)

    recovered = list(SpeechRecoveryRequest.objects.values_list("completed", "outcome__status"))
    switches = list(SpeechSwitchRequest.objects.values_list("closed", "outcome__status", "outcome__state__revision"))
    require(recovered == [(True, "reconciled")] and sorted(switches) == [(True, "succeeded", r) for r in range(1, 5)]
            and not Booking.objects.exists(), "Restore lost the staged audit history or created a booking.")
    print("Restored English and Polish drafts passed fixed Piper/Vosk requests with the controller stopped. Audit history preserved.")


if __name__ == "__main__":
    try:
        main()
    except FixtureFailure as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit(f"Isolated speech rollback failed ({type(error).__name__}).") from None
