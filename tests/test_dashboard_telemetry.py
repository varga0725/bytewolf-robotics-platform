"""The API owns the shared telemetry contract, independent of any dashboard."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from apps.api.server import create_app
from apps.api.telemetry import TelemetryFormatError, load_telemetry_snapshot


class ApiTelemetryContractTests(unittest.TestCase):
    def test_api_exposes_the_documented_bridge_shape(self) -> None:
        payload = {
            "position": {
                "latitude_deg": 47.4979,
                "longitude_deg": 19.0402,
                "absolute_altitude_m": 125.5,
                "relative_altitude_m": 2.4,
            },
            "battery": {"remaining_percent": 78.5},
            "in_air": True,
            "captured_at": "2026-07-16T10:51:11Z",
            "heading_deg": 90,
        }

        with self._telemetry_file(payload) as path:
            response = TestClient(create_app(path)).get("/api/v1/telemetry")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "position": payload["position"],
            "battery_percent": 78.5,
            "in_air": True,
            "captured_at": "2026-07-16T10:51:11Z",
            "heading_deg": 90.0,
        })

    def test_loader_accepts_mission_artifact_telemetry_without_inventing_state(self) -> None:
        with self._telemetry_file({"telemetry": {"battery_percent": 64}}) as path:
            snapshot = load_telemetry_snapshot(path)

        self.assertEqual(snapshot.battery_percent, 64.0)
        self.assertIsNone(snapshot.position)
        self.assertIsNone(snapshot.in_air)
        self.assertIsNone(snapshot.heading_deg)

    def test_api_reports_invalid_telemetry_as_unavailable(self) -> None:
        with self._telemetry_file("{invalid-json") as path:
            response = TestClient(create_app(path)).get("/api/v1/telemetry")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Telemetry file must contain valid JSON."})

    def test_loader_rejects_invalid_contract_values(self) -> None:
        payloads = (
            [],
            {"position": {"latitude_deg": 90.1, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5}},
            {"battery": {"remaining_percent": float("nan")}},
            {"captured_at": "2026-07-16T10:51:11"},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self._telemetry_file(payload) as path:
                with self.assertRaises(TelemetryFormatError):
                    load_telemetry_snapshot(path)

    @contextmanager
    def _telemetry_file(self, payload: object):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
            yield path


if __name__ == "__main__":
    unittest.main()
