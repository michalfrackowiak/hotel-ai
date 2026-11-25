"""Offline safety and HTTP-session regression checks for the staging probe."""

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic
import unittest
from unittest.mock import patch

import benchmark_mixed_load as benchmark


class MixedLoadTests(unittest.TestCase):
    def report(self):
        return {
            "cleanup": {"network": "removed"}, "production_before": {}, "production_after": {},
            "bookings": [{"completed": True, "language": language} for language in ("en", "pl")],
            "speech": {
                engine: [
                    {"language": language, "fixture_length": length, "requests": [{"status": 200}, {"status": 429}]}
                    for language in ("en", "pl") for length in ("short", "long")
                ] for engine in ("piper", "asr")
            },
            "ephemeral_database": {
                "bookings": 2, "clients": 2, "pending_confirmations": 2, "other_confirmations": 0, "admins": 0,
            },
        }

    def test_failure_gate_rejects_dispatch_resource_and_request_failures(self):
        good = self.report()
        self.assertFalse(benchmark.failed(good))
        for category, change in (
            ("db mismatch", lambda row: row["ephemeral_database"].update(bookings=3)),
            ("unexpected dispatch", lambda row: row["ephemeral_database"].update(other_confirmations=1)),
            ("admin seeded", lambda row: row["ephemeral_database"].update(admins=1)),
            ("load error", lambda row: row["speech"]["asr"][0]["requests"][0].update(status=500)),
            ("missing long Polish ASR", lambda row: row["speech"]["asr"].pop()),
            ("health error", lambda row: row.update(production_health_errors=1)),
            ("abort", lambda row: row.update(aborted_on_health=True)),
            ("short sustained load", lambda row: row.update(duration_seconds=600, load_wall_seconds=599)),
            ("unexpected running container", lambda row: row.update(running_containers_before=["web"],
                                                                    running_containers_after=["web", "other"])),
            ("no Polish booking", lambda row: row["bookings"].pop()),
            ("OOM", lambda row: row.update(resources_final={"web": {"memory.events": {"oom": 1}}})),
        ):
            with self.subTest(category=category):
                report = copy.deepcopy(good)
                change(report)
                self.assertTrue(benchmark.failed(report))

    def test_routed_budget_uses_actual_caps_and_requires_inactive_peers(self):
        web = {"running": True, "health": "healthy", "nano_cpus": 250_000_000}
        worker = {"running": True, "health": None, "nano_cpus": 400_000_000}
        services = benchmark.ROUTED_SERVICES
        snapshot = {name: {"running": False} for name in benchmark.INACTIVE_ROUTED_PEERS}
        snapshot.update({services["web"]: web, services["piper"]: worker.copy(), services["asr"]: worker.copy()})
        self.assertTrue(benchmark.production_ready(snapshot, services, True, 250_000_000, 2))
        self.assertFalse(benchmark.production_ready(snapshot, services, True, 250_000_000, 1))
        for name, change in (
            (services["web"], {"health": None}),
            (services["asr"], {"nano_cpus": 0}),
            (services["piper"], {"nano_cpus": 600_000_000}),
            (benchmark.INACTIVE_ROUTED_PEERS[0], {"running": True}),
        ):
            with self.subTest(name=name, change=change):
                rejected = copy.deepcopy(snapshot)
                rejected[name].update(change)
                self.assertFalse(benchmark.production_ready(rejected, services, True, 250_000_000, 2))

    def test_booking_uses_real_cookie_csrf_revision_and_contact_confirmation(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            index = 0

            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Set-Cookie", "csrftoken=fixture; Path=/")
                self.end_headers()
                self.wfile.write(b"fixture")

            def do_POST(self):
                if self.headers.get("X-CSRFToken") != "fixture" or "csrftoken=fixture" not in self.headers.get("Cookie", ""):
                    self.send_response(403)
                    self.end_headers()
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append(payload)
                stages = ("check_in", "check_out", "guests", "room", "confirm", "delivery", "email", "contact_confirm", "completed")
                if self.index and payload.get("revision") != str(self.index):
                    self.send_response(409)
                    self.end_headers()
                    return
                stage = stages[self.index]
                type(self).index += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"stage": stage, "revision": str(self.index)}).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            row = benchmark.booking_session(f"http://127.0.0.1:{server.server_port}", 0, "en", Event(), monotonic() + 10)
            self.assertTrue(row.get("completed"), row)
            self.assertEqual(captured[-2]["message"], "fixture0@example.test")
            self.assertEqual(captured[-1]["message"], "yes")
            self.assertEqual(len(row["turn_seconds"]), 9)
            self.assertEqual(row["turn_stages"][-2:], ["contact_confirm", "completed"])
        finally:
            server.shutdown()
            server.server_close()

    def test_aborted_booking_never_posts_a_turn(self):
        abort = Event()
        abort.set()
        with patch.object(benchmark, "build_opener") as opener:
            row = benchmark.booking_session("http://fixture/", 0, "en", abort, monotonic() + 1)
        self.assertNotIn("completed", row)
        self.assertEqual(opener.return_value.open.call_count, 0)


if __name__ == "__main__":
    unittest.main()
