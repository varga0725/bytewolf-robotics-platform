"""MAVSDK adapter for executing an approved, bounded mission on PX4."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import (
    TakeoffHoverLandMission,
    TakeoffReturnToHomeMission,
    TakeoffWaypointLandMission,
)
from brain.mission.runtime_policy import RuntimePolicy, load_runtime_policy
from brain.navigation.waypoints import (
    GlobalPosition,
    horizontal_distance_m,
    relative_waypoint_to_global,
)


class MavsdkAction(Protocol):
    async def set_takeoff_altitude(self, altitude_m: float) -> None: ...
    async def set_return_to_launch_altitude(self, altitude_m: float) -> None: ...
    async def arm(self) -> None: ...
    async def takeoff(self) -> None: ...
    async def land(self) -> None: ...
    async def return_to_launch(self) -> None: ...
    async def goto_location(
        self, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float, yaw_deg: float
    ) -> None: ...


class MavsdkCore(Protocol):
    def connection_state(self): ...


class MavsdkTelemetry(Protocol):
    def position(self): ...
    def in_air(self): ...


class MavsdkDrone(Protocol):
    action: MavsdkAction
    core: MavsdkCore
    telemetry: MavsdkTelemetry
    async def connect(self, system_address: str) -> None: ...


class MavsdkMissionAdapter:
    """Executes approved missions; it never retries an actuation command."""

    def __init__(
        self,
        drone: MavsdkDrone,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        runtime_policy: RuntimePolicy | None = None,
    ) -> None:
        self._drone = drone
        self._sleep = sleep
        self._runtime_policy = runtime_policy or load_runtime_policy()

    async def connect(self, system_address: str) -> None:
        await self._drone.connect(system_address=system_address)
        async for state in self._drone.core.connection_state():
            if state.is_connected:
                return

    async def execute(self, mission: TakeoffHoverLandMission) -> MissionExecution:
        """Execute takeoff-hover-land and confirm the normal landing by telemetry."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def goto_relative_waypoint(self, command: WaypointCommand) -> GlobalPosition:
        position = await anext(self._drone.telemetry.position())
        target = relative_waypoint_to_global(
            GlobalPosition(position.latitude_deg, position.longitude_deg, position.absolute_altitude_m),
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
        async def wait_for_match() -> None:
            async for position in self._drone.telemetry.position():
                current = GlobalPosition(
                    position.latitude_deg, position.longitude_deg, position.absolute_altitude_m
                )
                if (
                    horizontal_distance_m(current, target) <= tolerance_m
                    and abs(current.absolute_altitude_m - target.absolute_altitude_m) <= tolerance_m
                ):
                    return
            raise RuntimeError("PX4 position telemetry ended before reaching the waypoint.")

        await asyncio.wait_for(wait_for_match(), timeout=timeout_s)

    async def execute_waypoint_mission(
        self, mission: TakeoffWaypointLandMission
    ) -> MissionExecution:
        """Take off, visit one waypoint, then land with telemetry confirmation."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep(mission.takeoff_settle_seconds)
            execution = execution.transition(MissionPhase.NAVIGATING)
            target = await self.goto_relative_waypoint(mission.waypoint)
            await self.wait_until_waypoint_reached(
                target,
                mission.waypoint_tolerance_m,
                min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s),
            )
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def wait_until_landed(
        self, timeout_s: float, require_airborne_observation: bool = True
    ) -> None:
        """Wait for telemetry to observe a landed state within the bounded timeout."""
        async def wait_for_landing() -> None:
            observed_in_air = False
            async for is_in_air in self._drone.telemetry.in_air():
                observed_in_air = observed_in_air or is_in_air
                if not is_in_air and (observed_in_air or not require_airborne_observation):
                    return
            raise RuntimeError("PX4 in-air telemetry ended before confirming landing.")

        await asyncio.wait_for(wait_for_landing(), timeout=timeout_s)

    async def execute_return_to_home_mission(
        self, mission: TakeoffReturnToHomeMission
    ) -> MissionExecution:
        """Delegate return-and-land to PX4; use one land fallback only on failure."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        await self._drone.action.set_return_to_launch_altitude(mission.return_to_home.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep(mission.takeoff_settle_seconds)
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
            execution = execution.transition(MissionPhase.RETURNING)
            await self._drone.action.return_to_launch()
            await self.wait_until_landed(
                min(mission.landing_timeout_s, self._runtime_policy.landing_confirmation_timeout_s)
            )
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution,
                airborne,
                min(mission.landing_timeout_s, self._runtime_policy.landing_confirmation_timeout_s),
            )
            raise
        return execution.transition(MissionPhase.COMPLETED)

    async def _normal_land(
        self, execution: MissionExecution, confirmation_timeout_s: float
    ) -> MissionExecution:
        """Send the one normal land command and require telemetry confirmation."""
        execution = execution.transition(MissionPhase.LANDING)
        try:
            await self._drone.action.land()
            await self.wait_until_landed(confirmation_timeout_s, require_airborne_observation=False)
        except Exception:
            execution = execution.transition(MissionPhase.FAILED)
            raise
        return execution.transition(MissionPhase.COMPLETED)

    async def _fallback_land_after_airborne_failure(
        self, execution: MissionExecution, airborne: bool, confirmation_timeout_s: float
    ) -> MissionExecution:
        """Use exactly one fallback land after a pre-landing airborne failure."""
        if not airborne:
            return execution.transition(MissionPhase.FAILED)
        execution = execution.transition(MissionPhase.LANDING)
        try:
            await self._drone.action.land()
            await self.wait_until_landed(confirmation_timeout_s, require_airborne_observation=False)
        finally:
            execution = execution.transition(MissionPhase.FAILED)
        return execution
