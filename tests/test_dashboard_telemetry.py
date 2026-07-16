from __future__ import annotations

import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

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


if __name__ == "__main__":
    unittest.main()
