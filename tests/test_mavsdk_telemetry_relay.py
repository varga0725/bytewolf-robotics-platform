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
    remaining_percent = 0.78


class PercentageBattery:
    """SITL reports battery charge on the 0–100 MAVSDK boundary."""

    remaining_percent = 78.5


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
                return samples(type("InvalidBattery", (), {"remaining_percent": 1.2})())

        class InvalidDrone:
            telemetry = InvalidTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.json"
            destination.write_text('{"preserved": true}', encoding="utf-8")
            relay = MavsdkTelemetryRelay(InvalidDrone(), destination)

            with self.assertRaisesRegex(ValueError, "remaining_percent"):
                await relay.run_until_streams_complete()

            self.assertEqual(destination.read_text(encoding="utf-8"), '{"preserved": true}')

    async def test_normalizes_percentage_battery_samples_before_domain_routing(self) -> None:
        class PercentageTelemetry(Telemetry):
            def battery(self):
                return samples(PercentageBattery())

        class PercentageDrone:
            telemetry = PercentageTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemetry.json"
            relay = MavsdkTelemetryRelay(PercentageDrone(), destination)

            await relay.run_until_streams_complete()

            document = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(document["battery"], {"remaining_percent": 78.5})

    async def test_rejects_ambiguous_or_out_of_range_percentage_battery_samples(self) -> None:
        class InvalidPercentageTelemetry(Telemetry):
            def battery(self):
                return samples(type("InvalidBattery", (), {"remaining_percent": 100.1})())

        class InvalidPercentageDrone:
            telemetry = InvalidPercentageTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(InvalidPercentageDrone(), Path(directory) / "telemetry.json")

            with self.assertRaisesRegex(ValueError, "0.0 to 1.0"):
                await relay.run_until_streams_complete()

        class AmbiguousPercentageTelemetry(Telemetry):
            def battery(self):
                return samples(type("AmbiguousBattery", (), {"remaining_percent": 1.5})())

        class AmbiguousPercentageDrone:
            telemetry = AmbiguousPercentageTelemetry()

        with tempfile.TemporaryDirectory() as directory:
            relay = MavsdkTelemetryRelay(AmbiguousPercentageDrone(), Path(directory) / "telemetry.json")

            with self.assertRaisesRegex(ValueError, "ambiguous"):
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
                return samples(type("InvalidBattery", (), {"remaining_percent": 1.2})())

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
