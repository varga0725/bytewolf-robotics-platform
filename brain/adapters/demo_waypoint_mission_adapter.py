"""MAVSDK Mission-plugin execution for the demo-waypoint-mission route."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.sitl_target import require_px4_sitl_indication
from brain.navigation.waypoints import GlobalPosition, relative_waypoint_to_global

if TYPE_CHECKING:
    from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter


async def execute_demo_waypoint_mission(
    adapter: MavsdkMissionAdapter,
    config: DemoWaypointMissionConfig,
    *,
    action_timeout_s: float = 45.0,
) -> MissionExecution:
    from mavsdk.mission import MissionItem, MissionPlan

    await adapter._require_preflight()
    sitl_proof = await require_px4_sitl_indication(adapter._drone, timeout_s=action_timeout_s)
    adapter._demo_sitl_proof = sitl_proof
    home = await adapter._global_position_sample("home")
    execution = adapter._begin_execution()
    mission_uploaded = False
    armed = False
    airborne = False
    landing_timeout_s = min(config.landing_timeout_s, adapter._runtime_policy.landing_confirmation_timeout_s)
    rtl_wait_cap_s = max(config.rtl_timeout_s, landing_timeout_s + action_timeout_s + 5.0)
    adapter._demo_mission_items_reached = []
    adapter._start_peak_altitude_tracking()
    try:
        plan = _build_mission_plan(
            home,
            config,
            mission_item_type=MissionItem,
            mission_plan_type=MissionPlan,
        )
        adapter._demo_mission_plan_count = len(plan.mission_items)
        execution = adapter._record(execution, MissionPhase.TAKING_OFF)
        await asyncio.wait_for(
            adapter._drone.mission.set_return_to_launch_after_mission(True),
            timeout=config.upload_timeout_s,
        )
        await asyncio.wait_for(
            adapter._drone.mission.upload_mission(plan),
            timeout=config.upload_timeout_s,
        )
        mission_uploaded = True
        await asyncio.wait_for(adapter._drone.action.arm(), timeout=action_timeout_s)
        armed = True
        await asyncio.wait_for(adapter._drone.mission.start_mission(), timeout=action_timeout_s)
        airborne = True
        execution = adapter._record(execution, MissionPhase.NAVIGATING)
        await _wait_for_mission_items(
            adapter,
            total_items=len(plan.mission_items),
            mission_timeout_s=config.mission_timeout_s,
            progress_stall_timeout_s=config.progress_stall_timeout_s,
        )
        execution = adapter._record(execution, MissionPhase.RETURNING)
        await asyncio.wait_for(
            adapter.wait_until_landed(landing_timeout_s),
            timeout=rtl_wait_cap_s,
        )
        airborne = False
        landing_position = await adapter._global_position_sample("landing position")
        from brain.navigation.waypoints import horizontal_distance_m

        home_error_m = horizontal_distance_m(home, landing_position)
        if home_error_m > config.home_tolerance_m:
            raise RuntimeError(
                f"RTL landed {home_error_m:.1f} m from home; limit is {config.home_tolerance_m:.1f} m."
            )
        execution = adapter._record(execution, MissionPhase.COMPLETED)
        await adapter._demo_disarm_when_landed(landing_timeout_s, action_timeout_s)
    except Exception:
        if armed:
            await _demo_recover_mission_airborne(
                adapter,
                execution,
                airborne=airborne,
                armed=armed,
                mission_uploaded=mission_uploaded,
                landing_timeout_s=landing_timeout_s,
                action_timeout_s=action_timeout_s,
                rtl_timeout_s=config.rtl_timeout_s,
            )
        raise
    finally:
        await adapter._stop_peak_altitude_tracking()
        if mission_uploaded:
            try:
                await asyncio.wait_for(
                    adapter._drone.mission.clear_mission(),
                    timeout=config.upload_timeout_s,
                )
            except Exception:
                logging.getLogger(__name__).warning("clear_mission failed after demo waypoint run")
    return execution


def _build_mission_plan(
    home: GlobalPosition,
    config: DemoWaypointMissionConfig,
    *,
    mission_item_type,
    mission_plan_type,
):
    none_camera = mission_item_type.CameraAction.NONE
    takeoff_action = mission_item_type.VehicleAction.TAKEOFF
    cruise_action = mission_item_type.VehicleAction.NONE
    items = [
        mission_item_type(
            home.latitude_deg,
            home.longitude_deg,
            config.takeoff_altitude_m,
            config.cruise_speed_m_s,
            False,
            0.0,
            0.0,
            none_camera,
            0.0,
            0.0,
            config.acceptance_radius_m,
            0.0,
            0.0,
            takeoff_action,
        )
    ]
    for waypoint in config.waypoints:
        target = relative_waypoint_to_global(
            home,
            waypoint,
            current_relative_altitude_m=config.takeoff_altitude_m,
        )
        items.append(
            mission_item_type(
                target.latitude_deg,
                target.longitude_deg,
                waypoint.target_altitude_m,
                config.cruise_speed_m_s,
                True,
                0.0,
                0.0,
                none_camera,
                0.0,
                0.0,
                config.acceptance_radius_m,
                0.0,
                0.0,
                cruise_action,
            )
        )
    return mission_plan_type(items)


async def _wait_for_mission_items(
    adapter: MavsdkMissionAdapter,
    *,
    total_items: int,
    mission_timeout_s: float,
    progress_stall_timeout_s: float,
) -> None:
    last_current = -1
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    deadline = started_at + mission_timeout_s
    last_progress_at = started_at
    stream = adapter._drone.mission.mission_progress().__aiter__()

    while True:
        now = loop.time()
        if now > deadline:
            raise TimeoutError("Mission progress exceeded the overall mission timeout.")
        stall_remaining = progress_stall_timeout_s - (now - last_progress_at)
        overall_remaining = deadline - now
        wait_s = min(stall_remaining, overall_remaining)
        if wait_s <= 0:
            raise TimeoutError(
                f"Mission progress stalled at item {max(last_current, 0)}/{total_items}."
            )
        try:
            progress = await asyncio.wait_for(stream.__anext__(), timeout=wait_s)
        except StopAsyncIteration:
            raise RuntimeError("Mission progress stream ended before completion.") from None
        except asyncio.TimeoutError as error:
            raise TimeoutError(
                f"Mission progress stalled at item {max(last_current, 0)}/{total_items}."
            ) from error

        now = loop.time()
        if progress.current != last_current:
            adapter._demo_mission_items_reached.append(
                {
                    "index": int(progress.current),
                    "total": int(progress.total),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            last_current = int(progress.current)
            last_progress_at = now
        if progress.total > 0 and progress.current >= progress.total:
            return
    if not adapter._demo_mission_items_reached:
        raise RuntimeError("Mission progress stream produced no item-reached events.")


async def _demo_recover_mission_airborne(
    adapter: MavsdkMissionAdapter,
    execution: MissionExecution,
    *,
    airborne: bool,
    armed: bool,
    mission_uploaded: bool,
    landing_timeout_s: float,
    action_timeout_s: float,
    rtl_timeout_s: float,
) -> MissionExecution:
    if mission_uploaded:
        try:
            await asyncio.wait_for(adapter._drone.mission.pause_mission(), timeout=action_timeout_s)
        except Exception:
            pass
    return await adapter._demo_recover_airborne(
        execution,
        airborne=airborne,
        armed=armed,
        landing_timeout_s=landing_timeout_s,
        action_timeout_s=action_timeout_s,
        rtl_timeout_s=rtl_timeout_s,
    )
