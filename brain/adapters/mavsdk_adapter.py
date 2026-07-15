"""MAVSDK adapter for executing an approved, bounded mission on PX4."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import TakeoffHoverLandMission, TakeoffWaypointLandMission
from brain.navigation.waypoints import (
    GlobalPosition,
    horizontal_distance_m,
    relative_waypoint_to_global,
)


class MavsdkAction(Protocol):
    async def set_takeoff_altitude(self, altitude_m: float) -> None: ...

    async def arm(self) -> None: ...

    async def takeoff(self) -> None: ...

    async def land(self) -> None: ...

    async def goto_location(
        self, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float, yaw_deg: float
    ) -> None: ...


class MavsdkCore(Protocol):
    def connection_state(self): ...


class MavsdkTelemetry(Protocol):
    def position(self): ...


class MavsdkDrone(Protocol):
    action: MavsdkAction
    core: MavsdkCore
    telemetry: MavsdkTelemetry

    async def connect(self, system_address: str) -> None: ...


class MavsdkMissionAdapter:
    """Executes an already approved mission, never raw motor commands."""

    def __init__(
        self,
        drone: MavsdkDrone,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._drone = drone
        self._sleep = sleep

    async def connect(self, system_address: str) -> None:
        """Connect to the configured MAVLink endpoint and await discovery."""
        await self._drone.connect(system_address=system_address)
        async for state in self._drone.core.connection_state():
            if state.is_connected:
                return

    async def execute(self, mission: TakeoffHoverLandMission) -> MissionExecution:
        """Execute a mission and return its immutable audit trail."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
        except BaseException:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            finally:
                execution = execution.transition(MissionPhase.FAILED)
            raise
        else:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            except BaseException:
                execution = execution.transition(MissionPhase.FAILED)
                raise
            return execution.transition(MissionPhase.COMPLETED)

    async def goto_relative_waypoint(self, command: WaypointCommand) -> GlobalPosition:
        """Send an already safety-approved local waypoint to PX4 as a global target."""
        position = await anext(self._drone.telemetry.position())
        target = relative_waypoint_to_global(
            GlobalPosition(
                latitude_deg=position.latitude_deg,
                longitude_deg=position.longitude_deg,
                absolute_altitude_m=position.absolute_altitude_m,
            ),
            command,
            current_relative_altitude_m=position.relative_altitude_m,
        )
        await self._drone.action.goto_location(
            target.latitude_deg, target.longitude_deg, target.absolute_altitude_m, 0.0
        )
        return target

    async def wait_until_waypoint_reached(
        self, target: GlobalPosition, tolerance_m: float, timeout_s: float
    ) -> None:
        """Wait for GPS telemetry to confirm horizontal and altitude arrival."""
        async def wait_for_match() -> None:
            async for position in self._drone.telemetry.position():
                current = GlobalPosition(
                    latitude_deg=position.latitude_deg,
                    longitude_deg=position.longitude_deg,
                    absolute_altitude_m=position.absolute_altitude_m,
                )
                horizontal_error = horizontal_distance_m(current, target)
                altitude_error = abs(current.absolute_altitude_m - target.absolute_altitude_m)
                if horizontal_error <= tolerance_m and altitude_error <= tolerance_m:
                    return
            raise RuntimeError("PX4 position telemetry ended before reaching the waypoint.")

        await asyncio.wait_for(wait_for_match(), timeout=timeout_s)

    async def execute_waypoint_mission(
        self, mission: TakeoffWaypointLandMission
    ) -> MissionExecution:
        """Take off, visit one approved waypoint, hover, and land."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            await self._sleep(mission.takeoff_settle_seconds)
            execution = execution.transition(MissionPhase.NAVIGATING)
            target = await self.goto_relative_waypoint(mission.waypoint)
            await self.wait_until_waypoint_reached(
                target,
                tolerance_m=mission.waypoint_tolerance_m,
                timeout_s=mission.waypoint_timeout_s,
            )
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
        except BaseException:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            finally:
                execution = execution.transition(MissionPhase.FAILED)
            raise
        else:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            except BaseException:
                execution = execution.transition(MissionPhase.FAILED)
                raise
            return execution.transition(MissionPhase.COMPLETED)
