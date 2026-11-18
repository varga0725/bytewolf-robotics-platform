"""SITL identity must precede HOLD on the three SITL-only entry paths."""

from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from types import ModuleType, SimpleNamespace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.demo_waypoint_mission_adapter import execute_demo_waypoint_mission
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.acceptance_inflight_runner import connect_adapter
from brain.mission.demo_errors import DemoPointRtlError
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile

from tests.test_mavsdk_adapter_preflight_mode import (
    _LandModeDrone,
    _SwitchingLandDrone,
    _assert_no_flight_actions,
)


class SitlBeforePreflightSourceOrderTests(unittest.TestCase):
    _PATHS = (
        ("brain.adapters.mavsdk_adapter", "MavsdkMissionAdapter", "execute_demo_point_rtl_mission"),
        ("brain.adapters.demo_waypoint_mission_adapter", None, "execute_demo_waypoint_mission"),
        ("brain.mission.acceptance_inflight_runner", None, "connect_adapter"),
    )

    def test_sitl_gate_precedes_preflight_in_source(self) -> None:
        for module_name, class_name, symbol in self._PATHS:
            with self.subTest(symbol=symbol):
                module = __import__(module_name, fromlist=[symbol])
                target = getattr(module if class_name is None else getattr(module, class_name), symbol)
                source = inspect.getsource(target)
                sitl_index = source.index("require_px4_sitl")
                preflight_index = source.index("_require_preflight(allow_mode_change=True)")
                self.assertLess(sitl_index, preflight_index, source)

    def test_drift_check_precedes_preflight_in_demo_waypoint_source(self) -> None:
        source = inspect.getsource(execute_demo_waypoint_mission)
        preflight_index = source.index("await adapter._require_preflight(allow_mode_change=True)")
        drift_markers = ("_px4_home_reference", "assert_home_matches")
        self.assertTrue(
            all(source.index(marker) < preflight_index for marker in drift_markers),
            source,
        )


class SitlBeforePreflightBehaviourTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._sitl_patcher = patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            new_callable=AsyncMock,
            return_value={"status": "verified", "matched_signals": []},
        )
        self._sitl_patcher.start()
        self.addCleanup(self._sitl_patcher.stop)
    async def test_demo_point_rtl_failed_sitl_sends_no_hold_or_flight_commands(self) -> None:
        drone = _LandModeDrone("LAND")
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.0)
        mission = MagicMock(
            takeoff=MagicMock(target_altitude_m=2.0),
            return_to_home=MagicMock(target_altitude_m=2.0),
            waypoints=(),
            landing_timeout_s=5.0,
        )
        with patch.object(
            adapter,
            "_require_px4_sitl_before_arm",
            AsyncMock(side_effect=DemoPointRtlError("not sitl")),
        ):
            with self.assertRaises(DemoPointRtlError):
                await adapter.execute_demo_point_rtl_mission(mission, action_timeout_s=1.0)
        _assert_no_flight_actions(drone.events)

    async def test_demo_point_rtl_passing_sitl_still_hold_before_arm(self) -> None:
        drone = _SwitchingLandDrone()
        adapter = MavsdkMissionAdapter(MagicMock(), sleep=asyncio.sleep, preflight_wait_s=0.0)
        adapter._drone = drone
        adapter._global_position_sample = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(latitude_deg=0.0, longitude_deg=0.0, absolute_altitude_m=0.0)
        )
        adapter.goto_relative_waypoint = AsyncMock()  # type: ignore[method-assign]
        adapter.wait_until_waypoint_reached = AsyncMock()  # type: ignore[method-assign]
        adapter._sleep_with_runtime_watchdog = AsyncMock()  # type: ignore[method-assign]
        adapter.wait_until_landed = AsyncMock()  # type: ignore[method-assign]
        adapter._demo_disarm_when_landed = AsyncMock()  # type: ignore[method-assign]
        mission = MagicMock(
            takeoff=MagicMock(target_altitude_m=2.0),
            return_to_home=MagicMock(target_altitude_m=2.0),
            waypoints=(MagicMock(),),
            hover_duration_s=0.0,
            waypoint_tolerance_m=2.0,
            waypoint_timeout_s=5.0,
            landing_timeout_s=5.0,
            home_tolerance_m=6.0,
            takeoff_settle_seconds=0.0,
        )
        await adapter.execute_demo_point_rtl_mission(mission, action_timeout_s=2.0)
        self.assertEqual(drone.events.count("hold"), 1)
        self.assertIn("arm", drone.events)
        self.assertLess(drone.events.index("hold"), drone.events.index("arm"))

    async def test_demo_waypoint_failed_sitl_sends_no_commands(self) -> None:
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(takeoff_altitude_m=8.0, waypoints=())
        drone = MagicMock()
        drone.mission.upload_mission = AsyncMock()
        adapter = MavsdkMissionAdapter(drone, safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        preflight = AsyncMock()
        adapter._require_preflight = preflight
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(side_effect=DemoPointRtlError("not sitl")),
        ):
            with self.assertRaises(DemoPointRtlError):
                await execute_demo_waypoint_mission(adapter, config, action_timeout_s=1.0)
        preflight.assert_not_awaited()
        drone.mission.upload_mission.assert_not_awaited()

    async def test_demo_waypoint_passing_sitl_orders_checks_and_hold_before_arm(self) -> None:
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=(),
            upload_timeout_s=2.0,
            mission_timeout_s=1.0,
            progress_stall_timeout_s=1.0,
        )
        order: list[str] = []

        async def record_sitl(*_args, **_kwargs):
            order.append("sitl")
            return {"status": "verified", "matched_signals": []}

        drone = _LandModeDrone("LAND")
        adapter = MavsdkMissionAdapter(MagicMock(), preflight_wait_s=0.0)
        adapter._drone = drone
        bound_preflight = MavsdkMissionAdapter._require_preflight.__get__(adapter, MavsdkMissionAdapter)

        async def record_preflight(*, allow_mode_change: bool = False) -> None:
            order.append("preflight")
            await bound_preflight(allow_mode_change=allow_mode_change)
            raise RuntimeError("stop after hold")

        adapter._require_preflight = record_preflight  # type: ignore[method-assign]
        adapter._global_position_sample = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._start_peak_altitude_tracking = AsyncMock()  # type: ignore[method-assign]
        adapter._stop_peak_altitude_tracking = AsyncMock()  # type: ignore[method-assign]
        adapter._start_flight_observation_tracking = AsyncMock()  # type: ignore[method-assign]
        adapter._stop_flight_observation_tracking = AsyncMock()  # type: ignore[method-assign]
        mission_stub = MagicMock()
        mission_stub.set_return_to_launch_after_mission = AsyncMock()
        mission_stub.clear_mission = AsyncMock()
        drone.mission = mission_stub
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(side_effect=record_sitl),
        ):
            with self.assertRaises(RuntimeError) as error:
                await execute_demo_waypoint_mission(adapter, config, action_timeout_s=1.0)
            self.assertIn("stop after hold", str(error.exception))
        self.assertEqual(order[:2], ["sitl", "preflight"])
        self.assertEqual(drone.events, ["hold"])

    async def test_connect_adapter_failed_sitl_sends_no_hold(self) -> None:
        drone = _LandModeDrone("LAND")
        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter._require_preflight = AsyncMock()
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=drone)  # type: ignore[attr-defined]
        with (
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
            patch(
                "brain.mission.acceptance_inflight_runner.require_px4_sitl_indication",
                AsyncMock(side_effect=DemoPointRtlError("not sitl")),
            ),
            patch("brain.mission.acceptance_inflight_runner.MavsdkMissionAdapter", return_value=adapter),
        ):
            with self.assertRaises(DemoPointRtlError):
                await connect_adapter("udpin://127.0.0.1:14540", connection_timeout_s=1.0, preflight_wait_s=0.0, mavsdk_server_port=51000)
        adapter._require_preflight.assert_not_awaited()
        _assert_no_flight_actions(drone.events)

    async def test_connect_adapter_passing_sitl_hold_before_adapter_return(self) -> None:
        order: list[str] = []

        async def record_sitl(*_args, **_kwargs):
            order.append("sitl")
            return {"status": "verified", "matched_signals": []}

        drone = _LandModeDrone("LAND")
        drone.connect = AsyncMock()  # type: ignore[attr-defined]
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=drone)  # type: ignore[attr-defined]
        with (
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
            patch(
                "brain.mission.acceptance_inflight_runner.require_px4_sitl_indication",
                AsyncMock(side_effect=record_sitl),
            ),
        ):
            await connect_adapter("udpin://127.0.0.1:14540", connection_timeout_s=1.0, preflight_wait_s=0.0, mavsdk_server_port=51000)
        self.assertEqual(order, ["sitl"])
        self.assertEqual(drone.events, ["hold"])


    async def test_plan_home_drift_refusal_sends_no_hold(self) -> None:
        from apps.api.map_mission_home import MapHomeReference
        from brain.mission.demo_errors import DemoWaypointMissionError
        from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

        plan_home = MapHomeReference(
            latitude_deg=47.0,
            longitude_deg=8.0,
            absolute_altitude_m=500.0,
            captured_at="2026-01-01T00:00:00Z",
        )
        config = DemoWaypointMissionConfig(takeoff_altitude_m=8.0, waypoints=())
        drone = _LandModeDrone("LAND")
        position = SimpleNamespace(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        px4_home = SimpleNamespace(latitude_deg=47.0, longitude_deg=8.00003, absolute_altitude_m=500.0)

        async def position_stream():
            yield position

        async def home_stream():
            yield px4_home

        drone.telemetry.position = MagicMock(return_value=position_stream())  # type: ignore[method-assign]
        drone.telemetry.home = MagicMock(return_value=home_stream())  # type: ignore[method-assign]
        adapter = MavsdkMissionAdapter(MagicMock(), preflight_wait_s=0.0)
        adapter._drone = drone
        adapter._start_peak_altitude_tracking = AsyncMock()  # type: ignore[method-assign]
        adapter._stop_peak_altitude_tracking = AsyncMock()  # type: ignore[method-assign]
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": []}),
        ):
            with self.assertRaises(DemoWaypointMissionError):
                await execute_demo_waypoint_mission(adapter, config, plan_map_home=plan_home, action_timeout_s=2.0)
        _assert_no_flight_actions(drone.events)


if __name__ == "__main__":
    unittest.main()
