"""Map mission home anchoring and server-side N/E conversion."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.api.map_mission_home import (
    HOME_MISMATCH_THRESHOLD_M,
    assert_home_matches,
    load_snapshot_home,
    parse_map_home_reference,
    validate_plan_home_at_execution,
    waypoint_commands_from_geographic,
)
from apps.api.telemetry import load_telemetry_snapshot
from brain.mission.demo_errors import DemoWaypointMissionError
from brain.navigation.local_offset import global_position_to_local_offset
from brain.navigation.waypoints import GlobalPosition


def _telemetry_document() -> dict[str, object]:
    captured_at = "2026-11-15T10:00:00+00:00"
    return {
        "position": {
            "latitude_deg": 37.416,
            "longitude_deg": -122.049,
            "absolute_altitude_m": 12.0,
            "relative_altitude_m": 0.0,
        },
        "battery": {"remaining_percent": 88.0, "observed_at": captured_at},
        "in_air": False,
        "captured_at": captured_at,
        "home": {
            "latitude_deg": 37.416,
            "longitude_deg": -122.049,
            "absolute_altitude_m": 0.0,
            "captured_at": captured_at,
        },
    }


class MapMissionHomeTests(unittest.TestCase):
    def test_global_offset_round_trip(self) -> None:
        origin = GlobalPosition(37.416, -122.049, 0.0)
        north_m, east_m = 12.5, -3.0
        delta_lat = north_m / 6_371_000.0 * 57.29577951308232
        delta_lon = (
            east_m / (6_371_000.0 * __import__("math").cos(__import__("math").radians(37.416)))
            * 57.29577951308232
        )
        point = GlobalPosition(origin.latitude_deg + delta_lat, origin.longitude_deg + delta_lon, 0.0)
        converted = global_position_to_local_offset(origin, point)
        self.assertAlmostEqual(converted[0], north_m, places=2)
        self.assertAlmostEqual(converted[1], east_m, places=2)

    def test_waypoint_commands_from_geographic(self) -> None:
        captured_at = "2026-11-15T10:00:00+00:00"
        home = parse_map_home_reference(
            {
                "home": {
                    "latitude_deg": 37.416,
                    "longitude_deg": -122.049,
                    "absolute_altitude_m": 0.0,
                    "captured_at": captured_at,
                },
            },
        )
        commands = waypoint_commands_from_geographic(
            home=home,
            waypoints=(
                {"latitude_deg": 37.41609, "longitude_deg": -122.0489, "relative_altitude_m": 8.0},
            ),
            default_altitude_m=8.0,
        )
        self.assertEqual(len(commands), 1)
        north_m, east_m, altitude_m = commands[0]
        self.assertGreater(north_m, 0)
        self.assertGreater(east_m, 0)
        self.assertEqual(altitude_m, 8.0)

    def test_home_mismatch_rejected(self) -> None:
        captured_at = "2026-11-15T10:00:00+00:00"
        proposed = parse_map_home_reference(
            {
                "home": {
                    "latitude_deg": 37.416,
                    "longitude_deg": -122.049,
                    "absolute_altitude_m": 0.0,
                    "captured_at": captured_at,
                },
            },
        )
        authority = parse_map_home_reference(
            {
                "home": {
                    "latitude_deg": 37.426,
                    "longitude_deg": -122.049,
                    "absolute_altitude_m": 0.0,
                    "captured_at": captured_at,
                },
            },
        )
        with self.assertRaises(DemoWaypointMissionError) as error:
            assert_home_matches(proposed, authority=authority, threshold_m=HOME_MISMATCH_THRESHOLD_M)
        self.assertIn("differs from PX4 home", str(error.exception))

    def test_missing_home_in_snapshot_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.json"
            path.write_text(json.dumps({"in_air": False}), encoding="utf-8")
            with self.assertRaises(DemoWaypointMissionError):
                load_snapshot_home(path)
            snapshot = load_telemetry_snapshot(path)
            self.assertIsNone(snapshot.home)

    def test_execution_revalidates_stored_home(self) -> None:
        with TemporaryDirectory() as directory:
            telemetry_path = Path(directory) / "telemetry.json"
            telemetry_path.write_text(json.dumps(_telemetry_document()), encoding="utf-8")
            plan_path = Path(directory) / "demo.demo-waypoint-mission.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "map_home": {
                            "latitude_deg": 37.426,
                            "longitude_deg": -122.049,
                            "absolute_altitude_m": 0.0,
                            "captured_at": "2026-11-15T10:00:00+00:00",
                        },
                    },
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DemoWaypointMissionError):
                validate_plan_home_at_execution(plan_path, telemetry_path)