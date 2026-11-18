"""Plan map_home validation before the demo-waypoint CLI connects to PX4."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import fly_demo_waypoint_mission
from brain.mission.demo_errors import DemoWaypointMissionError
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class _FakeTelemetryRelay:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, stop_event: asyncio.Event) -> None:
        await stop_event.wait()


def _mavsdk_without_connect() -> tuple[ModuleType, MagicMock]:
    mavsdk = ModuleType("mavsdk")
    system_ctor = MagicMock()
    mavsdk.System = system_ctor  # type: ignore[attr-defined]
    return mavsdk, system_ctor


class FlyDemoWaypointPlanHomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_geo_plan_without_map_home_refuses_before_connect(self) -> None:
        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "route.demo-waypoint-mission.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "takeoff_altitude_m": 8.0,
                        "waypoints": [{"north_m": 0.0, "east_m": 0.0, "relative_altitude_m": 8.0}],
                        "geo_waypoints": [
                            {"latitude_deg": 47.0, "longitude_deg": 8.0, "relative_altitude_m": 8.0}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            arguments = fly_demo_waypoint_mission.parse_arguments(
                ("--plan-file", str(plan_path), "--deployment-mode", "simulation")
            )
            with (
                patch("brain.cli.fly_demo_waypoint_mission.require_mission_execution_enabled"),
                patch(
                    "brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval",
                ),
                patch("brain.cli.fly_demo_waypoint_mission.load_safety_profile"),
            ):
                mavsdk, system_ctor = _mavsdk_without_connect()
                with patch.dict(sys.modules, {"mavsdk": mavsdk}), self.assertRaisesRegex(
                    DemoWaypointMissionError, "missing map_home"
                ):
                    await fly_demo_waypoint_mission.run(arguments)
                system_ctor.assert_not_called()

    async def test_malformed_map_home_refuses_before_connect(self) -> None:
        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "route.demo-waypoint-mission.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "takeoff_altitude_m": 8.0,
                        "waypoints": [{"north_m": 0.0, "east_m": 0.0, "relative_altitude_m": 8.0}],
                        "geo_waypoints": [
                            {"latitude_deg": 47.0, "longitude_deg": 8.0, "relative_altitude_m": 8.0}
                        ],
                        "map_home": {"latitude_deg": "bad", "longitude_deg": 8.0, "captured_at": "2026-01-01T00:00:00Z"},
                    }
                ),
                encoding="utf-8",
            )
            arguments = fly_demo_waypoint_mission.parse_arguments(
                ("--plan-file", str(plan_path), "--deployment-mode", "simulation")
            )
            with (
                patch("brain.cli.fly_demo_waypoint_mission.require_mission_execution_enabled"),
                patch(
                    "brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval",
                ),
                patch("brain.cli.fly_demo_waypoint_mission.load_safety_profile"),
            ):
                mavsdk, system_ctor = _mavsdk_without_connect()
                with patch.dict(sys.modules, {"mavsdk": mavsdk}), self.assertRaises(DemoWaypointMissionError):
                    await fly_demo_waypoint_mission.run(arguments)
                system_ctor.assert_not_called()

    async def test_geo_plan_forwards_map_home_to_execute(self) -> None:
        captured: dict[str, object] = {}

        async def capture_execute(_adapter, _config, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop after capture")

        with TemporaryDirectory() as directory:
            plan_path = Path(directory) / "route.demo-waypoint-mission.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "takeoff_altitude_m": 8.0,
                        "waypoints": [{"north_m": 0.0, "east_m": 0.0, "relative_altitude_m": 8.0}],
                        "geo_waypoints": [
                            {"latitude_deg": 47.0, "longitude_deg": 8.0, "relative_altitude_m": 8.0}
                        ],
                        "map_home": {
                            "latitude_deg": 47.0,
                            "longitude_deg": 8.0,
                            "absolute_altitude_m": 500.0,
                            "captured_at": "2026-01-01T00:00:00Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            arguments = fly_demo_waypoint_mission.parse_arguments(
                ("--plan-file", str(plan_path), "--deployment-mode", "simulation")
            )
            adapter = MagicMock()
            adapter.connect = AsyncMock()
            home = MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)

            async def home_stream():
                yield home

            system = MagicMock()
            system.telemetry.home = MagicMock(return_value=home_stream())
            mavsdk, _ = _mavsdk_without_connect()
            mavsdk.System = MagicMock(return_value=system)  # type: ignore[attr-defined]
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch("brain.cli.fly_demo_waypoint_mission.require_mission_execution_enabled"),
                patch(
                    "brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval",
                ),
                patch(
                    "brain.cli.fly_demo_waypoint_mission.load_safety_profile",
                    return_value=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH),
                ),
                patch("brain.cli.fly_demo_waypoint_mission.file_sha256", return_value="abc"),
                patch("brain.cli.fly_demo_waypoint_mission.authorize_demo_waypoint_mission"),
                patch("brain.cli.fly_demo_waypoint_mission.require_simulation_target"),
                patch("brain.cli.fly_demo_waypoint_mission.acquire_px4_link"),
                patch("brain.cli.fly_demo_waypoint_mission.stop_owned_mavsdk_server"),
                patch("brain.cli.fly_demo_waypoint_mission.MavsdkMissionAdapter", return_value=adapter),
                patch("brain.cli.fly_demo_waypoint_mission.execute_demo_waypoint_mission", side_effect=capture_execute),
                patch("brain.cli.fly_demo_waypoint_mission.MavsdkTelemetryRelay", _FakeTelemetryRelay),
                patch(
                    "brain.cli.fly_demo_waypoint_mission.prepare_flight_run_recording",
                    return_value=MagicMock(run_id="test-run", telemetry_history_path=Path(directory) / "hist.jsonl"),
                ),
                patch("brain.cli.fly_demo_waypoint_mission.write_run_artifact"),
                patch("brain.cli.fly_demo_waypoint_mission.sample_positions_while", new=AsyncMock()),
            ):
                with self.assertRaises(RuntimeError):
                    await fly_demo_waypoint_mission.run(arguments)
        plan_map_home = captured.get("plan_map_home")
        self.assertIsNotNone(plan_map_home)
        assert plan_map_home is not None
        self.assertEqual(plan_map_home.latitude_deg, 47.0)
        self.assertEqual(plan_map_home.longitude_deg, 8.0)


if __name__ == "__main__":
    unittest.main()
