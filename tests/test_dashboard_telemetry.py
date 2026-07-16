from __future__ import annotations

import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from apps.dashboard.server import create_handler
from apps.dashboard.telemetry import TelemetryFormatError, load_telemetry_snapshot
from http.server import ThreadingHTTPServer


class DashboardTelemetryTests(unittest.TestCase):
    def test_loads_the_documented_bridge_shape(self) -> None:
        payload = {
            "position": {
                "latitude_deg": 47.4979,
                "longitude_deg": 19.0402,
                "absolute_altitude_m": 125.5,
                "relative_altitude_m": 2.4,
            },
            "battery": {"remaining_percent": 78.5},
            "in_air": True,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snapshot = load_telemetry_snapshot(path)

        self.assertEqual(snapshot.position.latitude_deg, 47.4979)
        self.assertEqual(snapshot.position.longitude_deg, 19.0402)
        self.assertEqual(snapshot.position.absolute_altitude_m, 125.5)
        self.assertEqual(snapshot.position.relative_altitude_m, 2.4)
        self.assertEqual(snapshot.battery_percent, 78.5)
        self.assertTrue(snapshot.in_air)

    def test_loads_adapter_artifact_telemetry_without_inventing_position(self) -> None:
        payload = {"telemetry": {"battery_percent": 64, "captured_at": "2026-07-16T10:51:11Z"}}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snapshot = load_telemetry_snapshot(path)

        self.assertIsNone(snapshot.position)
        self.assertEqual(snapshot.battery_percent, 64.0)
        self.assertIsNone(snapshot.in_air)

    def test_rejects_non_object_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(TelemetryFormatError, "object"):
                load_telemetry_snapshot(path)

    def test_preserves_capture_time_for_the_dashboard_freshness_indicator(self) -> None:
        payload = {"captured_at": "2026-07-16T10:51:11Z"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snapshot = load_telemetry_snapshot(path)

        self.assertEqual(snapshot.captured_at, "2026-07-16T10:51:11Z")

    def test_rejects_non_finite_numeric_telemetry(self) -> None:
        payloads = (
            {"position": {"latitude_deg": float("nan"), "longitude_deg": 19.0402, "absolute_altitude_m": 125.5}},
            {"position": {"latitude_deg": 47.4979, "longitude_deg": float("inf"), "absolute_altitude_m": 125.5}},
            {"position": {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": float("-inf")}},
            {"battery": {"remaining_percent": float("nan")}},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            for payload in payloads:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(TelemetryFormatError, "finite"):
                        load_telemetry_snapshot(path)

    def test_rejects_position_and_battery_values_outside_dashboard_contract(self) -> None:
        payloads = (
            {"position": {"latitude_deg": 90.1, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5}},
            {"position": {"latitude_deg": 47.4979, "longitude_deg": -180.1, "absolute_altitude_m": 125.5}},
            {"battery": {"remaining_percent": -0.1}},
            {"battery": {"remaining_percent": 100.1}},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            for payload in payloads:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(TelemetryFormatError):
                        load_telemetry_snapshot(path)

    def test_rejects_invalid_or_timezone_less_capture_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            for captured_at in ("not-a-timestamp", "2026-07-16T10:51:11"):
                with self.subTest(captured_at=captured_at):
                    path.write_text(json.dumps({"captured_at": captured_at}), encoding="utf-8")
                    with self.assertRaisesRegex(TelemetryFormatError, "captured_at"):
                        load_telemetry_snapshot(path)

    def test_dashboard_exposes_telemetry_and_rejects_control_requests(self) -> None:
        payload = {"in_air": False}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/") as response:
                    self.assertEqual(response.status, 200)
                    body = response.read()
                    self.assertIn(b"ByteWolf telemetry", body)
                    self.assertIn(b'id="connection"', body)
                    self.assertIn(b'id="relative-altitude"', body)
                    self.assertIn(b'id="flight-state"', body)
                with urlopen(f"{base_url}/api/telemetry") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), {
                        "position": None,
                        "battery_percent": None,
                        "in_air": False,
                        "captured_at": None,
                    })
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/not-a-control-endpoint", data=b"{}")
                self.assertEqual(error.exception.code, 405)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_dashboard_declares_all_connection_display_states(self) -> None:
        """Keep the browser UI's connection state machine regressions visible without a browser."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(json.dumps({"captured_at": "2026-07-16T10:51:11Z"}), encoding="utf-8")
            server, thread, base_url = self._start_server(path)
            try:
                with urlopen(f"{base_url}/") as response:
                    body = response.read().decode("utf-8")

                self.assertIn("['live','LIVE: telemetry connected']", body)
                self.assertIn("['stale',`STALE: ${Math.round(delta/1000)} seconds old`]", body)
                self.assertIn("['missing','MISSING: no capture timestamp']", body)
                self.assertIn("['error','INVALID: unreadable capture timestamp']", body)
                self.assertIn("['error','FUTURE: capture timestamp is ahead of this browser']", body)
                self.assertIn(".missing .dot", body)
            finally:
                self._stop_server(server, thread)

    def test_dashboard_http_api_preserves_live_stale_and_missing_payloads(self) -> None:
        cases = {
            "live": {"in_air": True, "captured_at": "2999-01-01T00:00:00Z"},
            "stale": {"in_air": False, "captured_at": "2000-01-01T00:00:00Z"},
            "missing": {"in_air": False},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            server, thread, base_url = self._start_server(path)
            try:
                for name, payload in cases.items():
                    with self.subTest(display_state=name):
                        path.write_text(json.dumps(payload), encoding="utf-8")
                        with urlopen(f"{base_url}/api/telemetry") as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.loads(response.read()), {
                                "position": None,
                                "battery_percent": None,
                                "in_air": payload["in_air"],
                                "captured_at": payload.get("captured_at"),
                            })
            finally:
                self._stop_server(server, thread)

    def test_dashboard_http_api_reports_invalid_telemetry_as_a_read_only_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text("{invalid-json", encoding="utf-8")
            server, thread, base_url = self._start_server(path)
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/api/telemetry")
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(json.loads(error.exception.read()), {
                    "error": "Telemetry file must contain valid JSON."
                })
            finally:
                self._stop_server(server, thread)

    def test_dashboard_rejects_every_mutating_http_method(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text("{}", encoding="utf-8")
            server, thread, base_url = self._start_server(path)
            try:
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    with self.subTest(method=method):
                        request = Request(
                            f"{base_url}/api/telemetry", data=b"{}", method=method
                        )
                        with self.assertRaises(HTTPError) as error:
                            urlopen(request)
                        self.assertEqual(error.exception.code, 405)
                        self.assertEqual(error.exception.read(), b"Read-only dashboard\n")
                        self.assertEqual(error.exception.headers["Allow"], "GET")
            finally:
                self._stop_server(server, thread)

    def _start_server(self, path: Path) -> tuple[ThreadingHTTPServer, Thread, str]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(path))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def _stop_server(self, server: ThreadingHTTPServer, thread: Thread) -> None:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    unittest.main()
