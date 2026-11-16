import json
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from .admission import evaluate_admission


IMAGE = "example.test/speech@sha256:" + "a" * 64
POLICY = {"host": {"daemon_id": "fixture-daemon", "topology_confirmed": True,
    "memory_reserve_mib": 512, "disk_reserve_mib": 256, "artifact_requirements_mib": {IMAGE: 1024}}}


def snapshot():
    return {"host": "host", "daemon_identity": {"connection": "pinned_local_socket", "os_type": "linux", "id": "fixture-daemon"},
        "host_resources": {"scope": "controller_host", "platform": "linux", "cpu_count": 2,
                           "memory_available_mib": 2560, "disk_free_mib": 1280}}


@override_settings(HOTEL_SPEECH_ADMISSION_POLICIES=json.dumps(POLICY))
class AdmissionTests(SimpleTestCase):
    def evaluate(self, data=None, images=(IMAGE, IMAGE)):
        planned = [SimpleNamespace(provider=SimpleNamespace(image=image)) for image in images]
        return evaluate_admission(data or snapshot(), planned, Decimal(2048), Decimal(2))

    def test_exact_boundaries_and_shared_image_counts_once(self):
        result = self.evaluate()
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["memory_required"], 2560)
        self.assertEqual((result["disk_required"], result["image_count"]), (1280, 1))
        for key, value in (("memory_available_mib", 2559), ("disk_free_mib", 1279), ("cpu_count", 1)):
            data = snapshot()
            data["host_resources"][key] = value
            with self.subTest(key=key):
                self.assertFalse(self.evaluate(data)["passed"])

    def test_full_active_growth_reservation_not_just_increment(self):
        data = snapshot()
        data["host_resources"]["memory_available_mib"] = 1024
        result = self.evaluate(data)
        self.assertFalse(result["passed"])
        self.assertEqual(result["memory_required"], 2560)
        self.assertTrue(any("full overlap" in issue for issue in result["issues"]))

    def test_unknown_identity_resources_artifacts_and_host_block(self):
        for key in ("daemon_identity", "host_resources"):
            data = snapshot()
            del data[key]
            self.assertFalse(self.evaluate(data)["passed"])
        for change in ({"connection": "unverified_context"}, {"id": "other"}, {"os_type": "windows"}):
            data = snapshot()
            data["daemon_identity"].update(change)
            self.assertFalse(self.evaluate(data)["passed"])
        for change in ({"platform": "unsupported"}, {"scope": "other"}, {"cpu_count": None},
                       {"memory_available_mib": None}, {"disk_free_mib": None}):
            data = snapshot()
            data["host_resources"].update(change)
            self.assertFalse(self.evaluate(data)["passed"])
        self.assertFalse(self.evaluate(snapshot() | {"host": "other"})["passed"])
        self.assertFalse(self.evaluate(images=("example.test/other@sha256:" + "b" * 64,))["passed"])

    def test_missing_or_malformed_policy_fails_without_echo(self):
        invalid = ["secret malformed", "[]", '{"host":{},"host":{}}', "[" * 2000 + "]" * 2000]
        for change in ({"topology_confirmed": False}, {"topology_confirmed": 1}, {"daemon_id": "secret bad"},
                       {"memory_reserve_mib": True}, {"disk_reserve_mib": 0}, {"disk_reserve_mib": 1.5},
                       {"memory_reserve_mib": float("inf")}, {"artifact_requirements_mib": {IMAGE: True}},
                       {"artifact_requirements_mib": {"secret:latest": 1}}, {"extra": "secret"}):
            invalid.append(json.dumps({"host": POLICY["host"] | change}))
        for raw in invalid:
            with self.subTest(raw=raw[:40]), override_settings(HOTEL_SPEECH_ADMISSION_POLICIES=raw):
                result = self.evaluate()
                self.assertFalse(result["passed"])
                self.assertNotIn("secret", str(result))
        with override_settings(HOTEL_SPEECH_ADMISSION_POLICIES="{}"):
            self.assertIn("No local", self.evaluate()["issues"][0])
