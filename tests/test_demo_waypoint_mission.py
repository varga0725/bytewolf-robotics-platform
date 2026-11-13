"""Multi-waypoint Monday demo: validation, API, adapter, acceptance, plots."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from apps.api.server import create_app
from brain.adapters.demo_waypoint_mission_adapter import execute_demo_waypoint_mission
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.commands import WaypointCommand
from brain.mission.demo_trajectory_plot import render_demo_trajectory_png
from brain.mission.demo_waypoint_mission import (
    MISSION_TYPE,
    authorize_demo_waypoint_mission,
    consecutive_acceptance_summary,
    default_demo_waypoint_loop,
    load_demo_waypoint_mission_config,
    validate_demo_waypoint_mission_progress,
)
from brain.mission.execution import MissionExecution, MissionPhase
from brain.safety.gate import FlightLimits, SafetyGate, SafetyViolation
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile

SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class DemoWaypointMissionValidationTests(unittest.TestCase):
    def test_default_loop_fits_gate(self) -> None:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        config = load_demo_waypoint_mission_config()
        gate = SafetyGate(profile.flight_limits())
        authorized = authorize_demo_waypoint_mission(gate, config)
        self.assertEqual(len(authorized.waypoints), 4)

    def test_any_single_item_geofence_violation_is_refused(self) -> None:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        limits = profile.flight_limits()
        gate = SafetyGate(limits)
        loop = default_demo_waypoint_loop(altitude_m=8.0, leg_m=8.0)
        bad = loop + (WaypointCommand(north_m=limits.max_distance_m + 5.0, east_m=0.0, target_altitude_m=8.0),)
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(takeoff_altitude_m=8.0, waypoints=bad, max_mission_items=20)
        with self.assertRaises(SafetyViolation):
            authorize_demo_waypoint_mission(gate, config)

    def test_altitude_violation_on_one_item_is_refused(self) -> None:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        gate = SafetyGate(profile.flight_limits())
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=(
                WaypointCommand(5.0, 0.0, profile.flight_limits().max_altitude_m + 1.0),
            ),
        )
        with self.assertRaises(SafetyViolation):
            authorize_demo_waypoint_mission(gate, config)


class DemoWaypointMissionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import apps.gateway.plan_execution as plan_execution

        plan_execution._active_execution = None
        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.client = TestClient(create_app(telemetry))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_review_registers_plan_without_execution(self) -> None:
        response = self.client.post(
            "/api/v1/missions/demo-waypoint-mission",
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mission_type"], MISSION_TYPE)
        self.assertTrue(body["approval_required"])

    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    @patch("apps.gateway.plan_execution.subprocess.Popen")
    @patch("apps.gateway.plan_execution.wait_for_free_link", return_value=True)
    def test_approve_spawns_waypoint_mission_cli(self, _wait, popen) -> None:
        plan_id = self.client.post(
            "/api/v1/missions/demo-waypoint-mission",
            headers={"X-ByteWolf-Session": SESSION},
        ).json()["plan_id"]
        popen.return_value = MagicMock(pid=42, poll=MagicMock(return_value=None))
        approved = self.client.post(
            "/api/v1/plans/approve",
            json={"plan_id": plan_id},
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(approved.status_code, 200)
        command = popen.call_args.args[0]
        self.assertIn("brain.cli.fly_demo_waypoint_mission", command)


class _MissionProgress:
    def __init__(self, current: int, total: int) -> None:
        self.current = current
        self.total = total


class _MissionStub:
    def __init__(self) -> None:
        self.upload_raises = False
        self.progress = [
            _MissionProgress(0, 3),
            _MissionProgress(1, 3),
            _MissionProgress(2, 3),
            _MissionProgress(3, 3),
        ]

    async def set_return_to_launch_after_mission(self, _enable: bool) -> None:
        return None

    async def upload_mission(self, _plan) -> None:
        if self.upload_raises:
            raise RuntimeError("upload failed")
        return None

    async def start_mission(self) -> None:
        return None

    async def pause_mission(self) -> None:
        return None

    async def clear_mission(self) -> None:
        return None

    def mission_progress(self):
        async def _stream():
            for step in self.progress:
                yield step

        return _stream()


class DemoWaypointMissionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_records_progress_and_disarms(self) -> None:
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:2],
            mission_timeout_s=5.0,
            progress_stall_timeout_s=3.0,
            upload_timeout_s=2.0,
            rtl_timeout_s=5.0,
            landing_timeout_s=5.0,
        )
        drone = MagicMock()
        mission_stub = _MissionStub()
        drone.mission = mission_stub
        mission_stub.clear_mission = AsyncMock()
        drone.action.arm = AsyncMock()
        adapter = MavsdkMissionAdapter(drone, safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._global_position_sample = AsyncMock(
            return_value=SimpleNamespace(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()
        adapter._start_peak_altitude_tracking = AsyncMock()
        adapter._stop_peak_altitude_tracking = AsyncMock()
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": ["a", "b"]}),
        ):
            execution = await execute_demo_waypoint_mission(adapter, config, action_timeout_s=2.0)
        validate_demo_waypoint_mission_progress(
            execution, items_reached=tuple(adapter._demo_mission_items_reached)
        )
        adapter._demo_disarm_when_landed.assert_awaited_once()
        mission_stub.clear_mission.assert_awaited()
        drone.action.arm.assert_awaited_once()

    async def test_upload_failure_triggers_recovery(self) -> None:
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:1],
            upload_timeout_s=2.0,
        )
        drone = MagicMock()
        stub = _MissionStub()

        async def failing_start() -> None:
            raise RuntimeError("start_mission failed")

        stub.start_mission = failing_start
        drone.mission = stub
        drone.action.arm = AsyncMock()
        adapter = MavsdkMissionAdapter(drone, safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._global_position_sample = AsyncMock(
            return_value=SimpleNamespace(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._demo_recover_airborne = AsyncMock(side_effect=lambda execution, **kwargs: execution)
        adapter._start_peak_altitude_tracking = AsyncMock()
        adapter._stop_peak_altitude_tracking = AsyncMock()
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": ["a", "b"]}),
        ):
            with self.assertRaises(RuntimeError):
                await execute_demo_waypoint_mission(adapter, config, action_timeout_s=1.0)
        adapter._demo_recover_airborne.assert_awaited()


class DemoWaypointRtlTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_rtl_landing_timeout_triggers_recovery(self) -> None:
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop()[:1],
            mission_timeout_s=3.0,
            progress_stall_timeout_s=2.0,
            upload_timeout_s=2.0,
            rtl_timeout_s=0.2,
            landing_timeout_s=1.0,
        )
        mission_stub = _MissionStub()
        drone = MagicMock()
        mission_stub.clear_mission = AsyncMock()
        drone.mission = mission_stub
        drone.action.arm = AsyncMock()
        adapter = MavsdkMissionAdapter(drone, safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._global_position_sample = AsyncMock(
            return_value=SimpleNamespace(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter.wait_until_landed = AsyncMock(side_effect=asyncio.TimeoutError)
        adapter._demo_recover_airborne = AsyncMock(side_effect=lambda execution, **kwargs: execution)
        adapter._start_peak_altitude_tracking = AsyncMock()
        adapter._stop_peak_altitude_tracking = AsyncMock()
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": ["a", "b"]}),
        ):
            with self.assertRaises((TimeoutError, asyncio.TimeoutError)):
                await execute_demo_waypoint_mission(adapter, config, action_timeout_s=1.0)
        adapter._demo_recover_airborne.assert_awaited()


class DemoWaypointProgressStallTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_stall_raises(self) -> None:
        from brain.adapters.demo_waypoint_mission_adapter import _wait_for_mission_items
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        class _StallMission:
            def mission_progress(self):
                async def _stream():
                    yield _MissionProgress(0, 3)
                    while True:
                        await asyncio.sleep(0.05)
                        yield _MissionProgress(0, 3)

                return _stream()

        adapter = MagicMock()
        adapter._demo_mission_items_reached = []
        adapter._drone = MagicMock(mission=_StallMission())
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop()[:1],
            mission_timeout_s=1.0,
            progress_stall_timeout_s=0.1,
        )
        with self.assertRaises((TimeoutError, asyncio.TimeoutError)):
            await _wait_for_mission_items(
                adapter,
                total_items=2,
                mission_timeout_s=config.mission_timeout_s,
                progress_stall_timeout_s=config.progress_stall_timeout_s,
            )


class AcceptanceLogicTests(unittest.TestCase):
    def test_two_good_and_one_bad_fails(self) -> None:
        runs = [{"overall_status": "passed"}, {"overall_status": "passed"}, {"overall_status": "failed"}]
        summary = consecutive_acceptance_summary(runs, required_runs=3)
        self.assertEqual(summary["overall_status"], "failed")

    def test_three_consecutive_passes_succeed(self) -> None:
        runs = [{"overall_status": "passed"}] * 3
        summary = consecutive_acceptance_summary(runs, required_runs=3)
        self.assertEqual(summary["overall_status"], "passed")


class TrajectoryPlotTests(unittest.TestCase):
    def test_render_trajectory_png(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "trajectory.png"
            positions = [
                {"north_m": 0.0, "east_m": 0.0, "relative_altitude_m": 0.0},
                {"north_m": 8.0, "east_m": 0.0, "relative_altitude_m": 8.0},
            ]
            items = [
                {"kind": "waypoint", "north_m": 8.0, "east_m": 0.0},
            ]
            render_demo_trajectory_png(positions=positions, mission_items=items, output_path=output)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
