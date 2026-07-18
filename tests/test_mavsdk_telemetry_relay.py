from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay


class Position:
    latitude_deg = 47.4979
    longitude_deg = 19.0402
    absolute_altitude_m = 125.5
    relative_altitude_m = 2.0


class Battery:
    """MAVSDK reports battery charge as a 0-100 percentage."""

    remaining_percent = 78.0


async def samples(*values: object):
    for value in values:
        yield value


class Telemetry:
    def position(self):
        return samples(Position())

    def battery(self):
        return samples(Battery())

    def in_air(self):
        return samples(True)


class ReadOnlyDrone:
    """Deliberately exposes telemetry only; flight actions cannot be invoked."""

    telemetry = Telemetry()


class MavsdkTelemetryRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_relays_all_declared_streams_to_an_atomic_dashboard_snapshot(self) -> None:
        captured_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.json"
            relay = MavsdkTelemetryRelay(
                ReadOnlyDrone(), destination, clock=lambda: captured_at
            )

            await relay.run_until_streams_complete()

            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8")),
                {
                    "position": {
                        "latitude_deg": 47.4979,
                        "longitude_deg": 19.0402,
                        "absolute_altitude_m": 125.5,
                        "relative_altitude_m": 2.0,
                    },
                    "battery": {"remaining_percent": 78.0},
                    "in_air": True,
                    "captured_at": "2026-07-16T12:00:00Z",
                },
            )
            self.assertEqual(list(Path(directory).glob(".telemetry-*.tmp")), [])

    async def test_rejects_invalid_samples_without_overwriting_last_safe_snapshot(self) -> None:
        class InvalidTelemetry(Telemetry):
            def battery(self):
                return samples(type("InvalidBattery", (), {"remaining_percent": float("nan")})())

        class InvalidDrone:
            telemetry = InvalidTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.json"
            destination.write_text('{"preserved": true}', encoding="utf-8")
            relay = MavsdkTelemetryRelay(InvalidDrone(), destination)

            with self.assertRaisesRegex(ValueError, "remaining_percent"):
                await relay.run_until_streams_complete()

            self.assertEqual(destination.read_text(encoding="utf-8"), '{"preserved": true}')

    async def test_passes_the_mavsdk_percentage_through_without_rescaling(self) -> None:
        """A near-empty battery must reach the dashboard as near-empty."""
        class LowBatteryTelemetry(Telemetry):
            def battery(self):
                return samples(type("LowBattery", (), {"remaining_percent": 1.0})())

        class LowBatteryDrone:
            telemetry = LowBatteryTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.json"
            relay = MavsdkTelemetryRelay(LowBatteryDrone(), destination)

            await relay.run_until_streams_complete()

            document = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(document["battery"], {"remaining_percent": 1.0})

    async def test_does_not_invent_battery_diagnostics_for_legacy_charge_only_samples(self) -> None:
        captured = []
        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(
                ReadOnlyDrone(), Path(directory) / "telemetry.json", on_event=captured.append
            )

            await relay.run_until_streams_complete()

        diagnostics = [
            event
            for event in captured
            if getattr(event, "source", None) == "MAVSDK telemetry.battery_diagnostics"
        ]
        self.assertEqual(diagnostics, [])

    async def test_records_extended_battery_diagnostics_when_the_adapter_provides_them(self) -> None:
        class DetailedBattery(Battery):
            id = 0
            voltage_v = 15.8
            current_battery_a = 4.2

        class DetailedTelemetry(Telemetry):
            def battery(self):
                return samples(DetailedBattery())

        class DetailedDrone:
            telemetry = DetailedTelemetry()

        captured = []
        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(
                DetailedDrone(), Path(directory) / "telemetry.json", on_event=captured.append
            )

            await relay.run_until_streams_complete()

        diagnostics = [
            event
            for event in captured
            if getattr(event, "source", None) == "MAVSDK telemetry.battery_diagnostics"
        ]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(
            dict(diagnostics[0].payload),
            {"id": 0, "voltage_v": 15.8, "current_battery_a": 4.2},
        )

    async def test_records_sitl_ground_truth_only_when_the_mavsdk_adapter_exposes_it(self) -> None:
        ground_truth = type(
            "GroundTruth",
            (),
            {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5},
        )()

        class GroundTruthTelemetry(Telemetry):
            def ground_truth(self):
                return samples(ground_truth)

        class GroundTruthDrone:
            telemetry = GroundTruthTelemetry()

        captured = []
        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(
                GroundTruthDrone(), Path(directory) / "telemetry.json", on_event=captured.append
            )

            await relay.run_until_streams_complete()

        evidence = [
            event
            for event in captured
            if getattr(event, "source", None) == "MAVSDK telemetry.ground_truth"
        ]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(dict(evidence[0].payload)["absolute_altitude_m"], 125.5)

    async def test_records_local_position_velocity_when_the_adapter_exposes_it(self) -> None:
        position = type("PositionNed", (), {"north_m": 2.0, "east_m": -1.0, "down_m": -3.0})()
        velocity = type(
            "VelocityNed", (), {"north_m_s": 0.5, "east_m_s": -0.25, "down_m_s": 0.1}
        )()
        sample = type("PositionVelocityNed", (), {"position": position, "velocity": velocity})()

        class LocalStateTelemetry(Telemetry):
            def position_velocity_ned(self):
                return samples(sample)

        class LocalStateDrone:
            telemetry = LocalStateTelemetry()

        captured = []
        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(
                LocalStateDrone(), Path(directory) / "telemetry.json", on_event=captured.append
            )

            await relay.run_until_streams_complete()

        evidence = [
            event
            for event in captured
            if getattr(event, "source", None) == "MAVSDK telemetry.position_velocity_ned"
        ]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(dict(evidence[0].payload)["north_m_s"], 0.5)

    async def test_rejects_a_battery_percentage_outside_the_mavsdk_range(self) -> None:
        class InvalidPercentageTelemetry(Telemetry):
            def battery(self):
                return samples(type("InvalidBattery", (), {"remaining_percent": 100.1})())

        class InvalidPercentageDrone:
            telemetry = InvalidPercentageTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(InvalidPercentageDrone(), Path(directory) / "telemetry.json")

            with self.assertRaisesRegex(ValueError, "0.0 and 100.0"):
                await relay.run_until_streams_complete()

    async def test_cancels_the_other_streams_when_one_stream_is_invalid(self) -> None:
        cancelled = asyncio.Event()

        async def endless_positions():
            try:
                while True:
                    yield Position()
                    await asyncio.sleep(1)
            finally:
                cancelled.set()

        class InvalidTelemetry(Telemetry):
            def position(self):
                return endless_positions()

            def battery(self):
                return samples(type("InvalidBattery", (), {"remaining_percent": float("nan")})())

        class InvalidDrone:
            telemetry = InvalidTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(InvalidDrone(), Path(directory) / "telemetry.json")
            with self.assertRaisesRegex(ValueError, "remaining_percent"):
                await relay.run_until_streams_complete()
            self.assertTrue(cancelled.is_set())

    async def test_uses_only_the_mavsdk_telemetry_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(ReadOnlyDrone(), Path(directory) / "telemetry.json")
            await relay.run_until_streams_complete()


if __name__ == "__main__":
    unittest.main()
