"""Append-only durable storage for validated telemetry events."""

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    route_mavsdk_telemetry,
)
from brain.telemetry.persistence import TelemetryHistoryStore, load_telemetry_history


class TelemetryHistoryStoreTests(unittest.TestCase):
    def test_persists_validated_events_in_append_order_and_replays_them_read_only(self) -> None:
        observed_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        events = (
            PositionTelemetryEvent("position", 47.5, 19.0, 125.0, 2.0, observed_at),
            BatteryTelemetryEvent("battery", 75.0, observed_at),
            FlightStateTelemetryEvent("flight_state", True, observed_at),
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            store = TelemetryHistoryStore(destination)
            for event in events:
                store.append(event)
            replayed = load_telemetry_history(destination)

        self.assertEqual(replayed, events)

    def test_rejects_corrupt_or_out_of_order_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            destination.write_text(
                '{"event_type":"battery","observed_at":"2026-07-18T10:00:05Z","remaining_percent":50,"topic":"battery","version":"v0.1"}\n'
                '{"event_type":"flight_state","in_air":true,"observed_at":"2026-07-18T10:00:00Z","topic":"flight_state","version":"v0.1"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "out of chronological order"):
                load_telemetry_history(destination)

    def test_rejects_non_finite_and_out_of_range_values(self) -> None:
        cases = (
            ('{"absolute_altitude_m":125,"event_type":"position","latitude_deg":1e309,"longitude_deg":19,"observed_at":"2026-07-18T10:00:00Z","relative_altitude_m":2,"topic":"position","version":"v0.1"}\n', "finite"),
            ('{"event_type":"battery","observed_at":"2026-07-18T10:00:00Z","remaining_percent":100.1,"topic":"battery","version":"v0.1"}\n', "between"),
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            for payload, error in cases:
                with self.subTest(payload=payload):
                    destination.write_text(payload, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        load_telemetry_history(destination)

    def test_rejects_history_from_a_different_recorded_run(self) -> None:
        observed_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            TelemetryHistoryStore(destination, run_id="first-run").append(
                BatteryTelemetryEvent("battery", 75.0, observed_at)
            )

            with self.assertRaisesRegex(ValueError, "different run"):
                load_telemetry_history(destination, expected_run_id="second-run")

    def test_reloads_validated_supplemental_history_against_their_declared_schema(self) -> None:
        observed_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        events = (
            route_mavsdk_telemetry(
                "MAVSDK telemetry.ground_truth",
                type(
                    "GroundTruth",
                    (),
                    {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5},
                )(),
                observed_at=observed_at,
            ),
            route_mavsdk_telemetry(
                "MAVSDK telemetry.position_velocity_ned",
                type(
                    "PositionVelocityNed",
                    (),
                    {
                        "position": type(
                            "PositionNed", (), {"north_m": 2.0, "east_m": -1.0, "down_m": -3.0}
                        )(),
                        "velocity": type(
                            "VelocityNed",
                            (),
                            {"north_m_s": 0.5, "east_m_s": -0.25, "down_m_s": 0.1},
                        )(),
                    },
                )(),
                observed_at=observed_at,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            store = TelemetryHistoryStore(destination)
            for event in events:
                store.append(event)
            replayed = load_telemetry_history(destination)

        self.assertEqual(replayed, events)

    def test_rejects_supplemental_history_that_no_longer_matches_its_declared_stream_schema(self) -> None:
        payload = (
            '{"event_type":"supplemental","observed_at":"2026-07-18T10:00:00Z","payload":{"north_m":2.0,"east_m":-1.0,"down_m":-3.0},'
            '"source":"MAVSDK telemetry.position_velocity_ned","topic":"telemetry/history/position_velocity_ned","version":"v0.1"}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.jsonl"
            destination.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match|north_m_s"):
                load_telemetry_history(destination)


if __name__ == "__main__":
    unittest.main()
