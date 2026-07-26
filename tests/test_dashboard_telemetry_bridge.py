"""The dashboard must see the vehicle whenever the simulator is up.

Before this bridge existed, `live-telemetry.json` was written only by a flight
CLI, so a running simulator with no mission in progress left the dashboard
showing an old snapshot. That reads as "the app cannot see the drone", and it
was an accurate reading.

The bridge is a monitor, not a control path: it holds no mission, no gate and
no adapter, and it must never gain the ability to command anything.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.cli import dashboard_telemetry
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay


class Position:
    latitude_deg = 47.4
    longitude_deg = 19.0
    absolute_altitude_m = 120.0
    relative_altitude_m = 2.5


class Battery:
    remaining_percent = 88.0


async def _samples(*values: object):
    for value in values:
        yield value
        await asyncio.sleep(0)


class Telemetry:
    def position(self):
        return _samples(Position())

    def battery(self):
        return _samples(Battery())

    def in_air(self):
        return _samples(True)


class ReadOnlyDrone:
    telemetry = Telemetry()


class BridgeArgumentTests(unittest.TestCase):
    def test_the_bridge_defaults_to_the_dashboard_snapshot_the_ui_reads(self) -> None:
        parsed = dashboard_telemetry.parse_arguments([])

        self.assertEqual(parsed.snapshot_file, dashboard_telemetry.DEFAULT_SNAPSHOT_PATH)
        self.assertEqual(parsed.endpoint, "udpin://0.0.0.0:14540")
        self.assertIsNone(parsed.seconds, "without a limit it runs until interrupted")

    def test_the_endpoint_and_destination_can_be_moved(self) -> None:
        parsed = dashboard_telemetry.parse_arguments(
            ["--endpoint", "udpin://0.0.0.0:14550", "--snapshot-file", "/tmp/x.json", "--seconds", "5"]
        )

        self.assertEqual(parsed.endpoint, "udpin://0.0.0.0:14550")
        self.assertEqual(parsed.snapshot_file, Path("/tmp/x.json"))
        self.assertEqual(parsed.seconds, 5.0)


class BridgeControlSurfaceTests(unittest.TestCase):
    """A telemetry bridge that could arm something would be a control path."""

    def test_the_module_imports_no_flight_control_surface(self) -> None:
        """Checked on the parsed code, not the prose: the docstring may name what it refuses."""
        import ast

        tree = ast.parse(Path(dashboard_telemetry.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)

        for forbidden in ("action", "adapter", "gate", "mission_spec"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    [name for name in imported if forbidden in name.lower()],
                    f"a telemetry bridge must not import a {forbidden} surface",
                )

        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(called & {"arm", "takeoff", "land", "goto_location", "start_mission"}, set())


class BridgeRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_snapshot_goes_live_without_any_mission_running(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            relay = MavsdkTelemetryRelay(ReadOnlyDrone(), destination, clock=lambda: datetime.now(UTC))

            await relay.run_until_streams_complete()

            document = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(document["in_air"], True)
        self.assertEqual(document["battery"]["remaining_percent"], 88.0)
        self.assertIn("captured_at", document)

    async def test_an_incomplete_link_leaves_the_previous_snapshot_alone(self) -> None:
        """Half a vehicle is not a vehicle: no position means no new snapshot."""
        class PositionlessTelemetry(Telemetry):
            def position(self):
                return _samples()

        class PositionlessDrone:
            telemetry = PositionlessTelemetry()

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            destination.write_text('{"kept": true}', encoding="utf-8")

            await MavsdkTelemetryRelay(PositionlessDrone(), destination).run_until_streams_complete()

            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"kept": True})


if __name__ == "__main__":
    unittest.main()
