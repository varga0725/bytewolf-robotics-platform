"""USB-only Pixhawk diagnostics are observable, never a control surface."""

from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import check_usb_bench


async def _samples(*values: object):
    for value in values:
        yield value


class _Connected:
    is_connected = True


class _Health:
    is_global_position_ok = False
    is_home_position_ok = False
    is_armable = False


class _Position:
    latitude_deg = 47.4979
    longitude_deg = 19.0402
    absolute_altitude_m = 112.5
    relative_altitude_m = 0.0


class _Gps:
    num_satellites = 0
    fix_type = "NO_GPS"


class _BatteryWithoutPack:
    remaining_percent = float("nan")


class _Telemetry:
    def health(self):
        return _samples(_Health())

    def position(self):
        return _samples(_Position())

    def gps_info(self):
        return _samples(_Gps())

    def battery(self):
        return _samples(_BatteryWithoutPack())

    def armed(self):
        return _samples(False)

    def in_air(self):
        return _samples(False)


class UsbBenchCliTests(unittest.TestCase):
    def test_module_has_no_flight_control_import_or_call_surface(self) -> None:
        tree = ast.parse(Path(check_usb_bench.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("brain.adapters.mavsdk_adapter", imported)
        self.assertFalse(any(module.startswith("brain.mission") for module in imported))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(called & {"arm", "takeoff", "land", "return_to_launch", "goto_location"}, set())

    def test_serial_endpoint_and_positive_timeouts_are_a_cli_contract(self) -> None:
        arguments = check_usb_bench.parse_arguments((
            "--endpoint", "serial:///dev/cu.usbmodem01:57600", "--connection-timeout", "4", "--sample-timeout", "2",
        ))

        self.assertEqual(arguments.endpoint, "serial:///dev/cu.usbmodem01:57600")
        self.assertEqual(arguments.connection_timeout, 4.0)
        self.assertEqual(arguments.sample_timeout, 2.0)
        for invalid in ("0", "-1", "nan", "inf"):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                check_usb_bench.parse_arguments(("--sample-timeout", invalid))

    def test_batteryless_usb_bench_records_observations_and_never_touches_action(self) -> None:
        system = MagicMock()
        system.connect = AsyncMock()
        system.core.connection_state.return_value = _samples(_Connected())
        system.telemetry = _Telemetry()
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=system)  # type: ignore[attr-defined]

        with TemporaryDirectory() as directory:
            arguments = check_usb_bench.parse_arguments((
                "--endpoint", "serial:///dev/cu.usbmodem01:57600", "--artifact-dir", directory,
                "--mavsdk-server-port", "51004",
            ))
            with patch.dict(sys.modules, {"mavsdk": mavsdk}):
                asyncio.run(check_usb_bench.run(arguments))
            artifact = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))

        system.connect.assert_awaited_once_with(system_address="serial:///dev/cu.usbmodem01:57600")
        self.assertEqual(artifact["outcome"], "completed")
        self.assertEqual(artifact["flight_readiness"]["status"], "blocked")
        self.assertEqual(artifact["checks"]["battery"]["status"], "invalid")
        self.assertFalse(artifact["checks"]["armed"]["value"])
        system.action.assert_not_called()
        system._stop_mavsdk_server.assert_called_once()

    def test_connection_failure_writes_a_failed_artifact_and_still_never_commands(self) -> None:
        system = MagicMock()
        system.connect = AsyncMock(side_effect=TimeoutError("link unavailable"))
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=system)  # type: ignore[attr-defined]

        with TemporaryDirectory() as directory:
            arguments = check_usb_bench.parse_arguments(("--artifact-dir", directory))
            with patch.dict(sys.modules, {"mavsdk": mavsdk}), self.assertRaisesRegex(TimeoutError, "link unavailable"):
                asyncio.run(check_usb_bench.run(arguments))
            artifact = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))

        self.assertEqual(artifact["outcome"], "failed")
        self.assertIn("link unavailable", artifact["failure_reason"])
        system.action.assert_not_called()
        system._stop_mavsdk_server.assert_called_once()


if __name__ == "__main__":
    unittest.main()
