"""Validate routed speech and pinned web images offline with fictional settings."""

import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "hotel-routed-fixture"
BASE_FILES = ("compose.yaml", "compose.server.yaml")
ROUTED_FILES = (*BASE_FILES, "compose.speech-routed.yaml")
FIXED_FILES = (*BASE_FILES, "compose.piper.yaml", "compose.asr.yaml")
IMAGE_FILE = "compose.web-image.yaml"
WEB_IMAGE_ID = "sha256:" + "c" * 64
OWNER = "hotel-ai-routed"
TRAFFIC_DIRECTORY = "/var/lib/hotel-routing-fixture"
TRAFFIC_MOUNT = "/run/hotel-ai-traffic"
SUBNET = "10.91.84.0/28"
IMAGE_IDS = {"piper": "sha256:" + "a" * 64, "vosk": "sha256:" + "b" * 64}
WORKERS = {
    ("piper", "primary"): "10.91.84.2",
    ("piper", "standby"): "10.91.84.3",
    ("vosk", "primary"): "10.91.84.4",
    ("vosk", "standby"): "10.91.84.5",
}
TARGETS = [
    {
        "name": f"{OWNER}-{model}-{role}",
        "task": "tts" if model == "piper" else "stt",
        "base_url": f"http://{address}:" + ("5000" if model == "piper" else "5001"),
        "languages": ["en", "pl"],
    }
    for (model, role), address in WORKERS.items()
]
ENVIRONMENT = {
    "DJANGO_SECRET_KEY": "django-routed-compose-fixture",
    "HOTEL_WEB_IMAGE": WEB_IMAGE_ID,
    "HOTEL_ROUTED_TRAFFIC_DIR": TRAFFIC_DIRECTORY,
    "HOTEL_ROUTED_SPEECH_SUBNET": SUBNET,
    "HOTEL_SPEECH_ROUTE_TARGETS": json.dumps(TARGETS),
    "HOTEL_SPEECH_CONTROL_TOKEN": "routed-compose-fixture-token-only",
    "HOTEL_SPEECH_CONTROL_HOST": PROJECT,
    "HOTEL_SPEECH_CONTROL_SWITCH_ENABLED": "1",
    "HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED": "1",
    **{f"HOTEL_ROUTED_{model.upper()}_IMAGE": image for model, image in IMAGE_IDS.items()},
    **{f"HOTEL_ROUTED_{model.upper()}_{role.upper()}_IP": address for (model, role), address in WORKERS.items()},
}


def compose_config(*profiles, files=ROUTED_FILES, omit=None):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DJANGO_", "HOTEL_", "BOOKING_", "COMPOSE_", "DOCKER_"))}
    env.update(ENVIRONMENT)
    if omit:
        del env[omit]
    with tempfile.TemporaryDirectory() as temporary:
        env_file = Path(temporary) / "fixture.env"
        env_file.touch()
        command = ["docker", "compose", "--project-name", PROJECT, "--env-file", str(env_file)]
        for file in files:
            command += ["-f", file]
        for profile in profiles:
            command += ["--profile", profile]
        return subprocess.run(
            command + ["config", "--format", "json", "--no-path-resolution"],
            cwd=ROOT, env=env, check=False, capture_output=True, text=True,
        )


class RoutedComposeTests(unittest.TestCase):
    def render(self, *profiles, files=ROUTED_FILES):
        result = compose_config(*profiles, files=files)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_web_remains_private_and_preserves_booking_data(self):
        projection = self.render()
        self.assertEqual(set(projection["services"]), {"web"})
        web = projection["services"]["web"]
        self.assertEqual(web["network_mode"], "host")
        self.assertFalse(web.get("ports"))
        self.assertFalse(web.get("networks"))
        self.assertEqual(web["user"], "10001:10001")
        self.assertEqual(float(web["cpus"]), 0.25)
        self.assertEqual(int(web["mem_limit"]), 512 * 1024**2)
        self.assertEqual(web["memswap_limit"], web["mem_limit"])
        self.assertEqual(web["command"], [
            "sh", "-ec", "python manage.py migrate --noinput && "
            "exec gunicorn config.wsgi:application --bind 127.0.0.1:8080 --workers 1 --threads 4",
        ])
        self.assertIn("http://127.0.0.1:8080/healthz/", web["healthcheck"]["test"][-1])
        self.assertEqual(projection["volumes"]["hotel-data"]["name"], f"{PROJECT}_hotel-data")
        self.assertEqual(
            {volume["target"]: (volume["type"], volume["source"]) for volume in web["volumes"]},
            {"/app/data": ("volume", "hotel-data"), TRAFFIC_MOUNT: ("bind", TRAFFIC_DIRECTORY)},
        )
        mount = next(volume for volume in web["volumes"] if volume["target"] == TRAFFIC_MOUNT)
        self.assertFalse(mount.get("read_only"))
        self.assertIs(mount["bind"].get("create_host_path"), False)
        expected_environment = {
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": ENVIRONMENT["DJANGO_SECRET_KEY"],
            "HOTEL_SPEECH_TRAFFIC_DB": f"{TRAFFIC_MOUNT}/traffic.sqlite3",
            "HOTEL_SPEECH_ROUTE_TARGETS": ENVIRONMENT["HOTEL_SPEECH_ROUTE_TARGETS"],
            "HOTEL_SPEECH_CONTROL_URL": "http://127.0.0.1:8093",
            "HOTEL_SPEECH_CONTROL_TOKEN": ENVIRONMENT["HOTEL_SPEECH_CONTROL_TOKEN"],
            "HOTEL_SPEECH_CONTROL_HOST": PROJECT,
            "HOTEL_SPEECH_CONTROL_SWITCH_ENABLED": "0",
            "HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED": "0",
        }
        for setting, value in expected_environment.items():
            self.assertEqual(web["environment"].get(setting), value, setting)

    def test_each_worker_pair_is_opt_in_and_private(self):
        for role in ("primary", "standby"):
            with self.subTest(role=role):
                projection = self.render(f"routed-{role}")
                self.assertEqual(set(projection["services"]), {"web", f"routed-piper-{role}", f"routed-vosk-{role}"})
        projection = self.render("*")
        self.assertEqual(
            set(projection["services"]),
            {"web"} | {f"routed-{model}-{role}" for model, role in WORKERS},
        )
        network = projection["networks"]["routed-speech"]
        self.assertEqual(network["driver"], "bridge")
        self.assertIs(network["internal"], True)
        self.assertEqual(network["ipam"]["config"], [{"subnet": SUBNET}])
        subnet = ipaddress.ip_network(SUBNET)
        available_addresses = set(subnet.hosts()) - {subnet.network_address + 1}
        addresses = set()
        for (model, role), address in WORKERS.items():
            name = f"routed-{model}-{role}"
            with self.subTest(worker=name):
                worker = projection["services"][name]
                self.assertEqual(worker["container_name"], f"hotel-ai-{name}")
                self.assertEqual(worker["profiles"], [f"routed-{role}"])
                self.assertEqual(worker["image"], IMAGE_IDS[model])
                self.assertEqual(worker["pull_policy"], "never")
                self.assertEqual(worker["restart"], "no")
                self.assertEqual(worker["labels"]["hotel-ai.controller-owner"], OWNER)
                for setting in ("build", "ports", "volumes", "privileged", "depends_on", "network_mode"):
                    self.assertFalse(worker.get(setting), setting)
                self.assertIs(worker["init"], True)
                self.assertIs(worker["read_only"], True)
                self.assertEqual(worker["user"], "10001:10001")
                self.assertEqual(worker["cap_drop"], ["ALL"])
                self.assertEqual(worker["security_opt"], ["no-new-privileges"])
                self.assertEqual(float(worker["cpus"]), 0.4)
                self.assertEqual(int(worker["mem_limit"]), (1024 if model == "piper" else 1536) * 1024**2)
                self.assertEqual(worker["memswap_limit"], worker["mem_limit"])
                self.assertEqual(worker["networks"], {"routed-speech": {"ipv4_address": address}})
                self.assertIn(ipaddress.ip_address(address), available_addresses)
                addresses.add(address)
        self.assertEqual(len(addresses), len(WORKERS))

    def test_missing_private_input_prevents_rendering(self):
        result = compose_config(omit="HOTEL_ROUTED_TRAFFIC_DIR")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOTEL_ROUTED_TRAFFIC_DIR", result.stderr)

    def test_pinned_web_image_only_changes_web_image_selection(self):
        for files, profiles in ((ROUTED_FILES, ("*",)), (FIXED_FILES, ())):
            with self.subTest(files=files):
                original = self.render(*profiles, files=files)
                pinned = self.render(*profiles, files=(*files, IMAGE_FILE))
                original_web = original["services"]["web"]
                self.assertIn("build", original_web)
                del original_web["build"]
                original_web.update(image=WEB_IMAGE_ID, pull_policy="never")
                self.assertEqual(pinned, original)

    def test_pinned_web_image_requires_explicit_input(self):
        result = compose_config(files=(*FIXED_FILES, IMAGE_FILE), omit="HOTEL_WEB_IMAGE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOTEL_WEB_IMAGE", result.stderr)

    def test_systemd_disables_only_web_auto_restart(self):
        files = (*ROUTED_FILES, IMAGE_FILE)
        original = self.render("*", files=files)
        self.assertEqual(original["services"]["web"]["restart"], "unless-stopped")
        original["services"]["web"]["restart"] = "no"
        self.assertEqual(self.render("*", files=(*files, "compose.speech-systemd.yaml")), original)


if __name__ == "__main__":
    unittest.main()
