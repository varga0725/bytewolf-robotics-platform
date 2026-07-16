"""MAVSDK adapter for executing an approved, bounded mission on PX4."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from math import isfinite
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
from brain.safety.profile import SafetyProfile, load_safety_profile


class MissionPreflightError(RuntimeError):
    """Raised when required PX4 telemetry cannot authorize a mission start."""


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
    def health(self): ...
    def home(self): ...
    def battery(self): ...


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
        safety_profile: SafetyProfile | None = None,
    ) -> None:
        self._drone = drone
        self._sleep = sleep
        self._runtime_policy = runtime_policy or load_runtime_policy()
        self._safety_profile = safety_profile or load_safety_profile()

    async def connect(self, system_address: str) -> None:
        await self._drone.connect(system_address=system_address)
        async for state in self._drone.core.connection_state():
            if state.is_connected:
                return

    async def execute(self, mission: TakeoffHoverLandMission) -> MissionExecution:
        """Execute takeoff-hover-land and confirm the normal landing by telemetry."""
        await self._require_preflight()
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
        await self._require_preflight()
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
        await self._require_preflight()
        home = await self._global_position_sample("home")
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
            airborne = False
            landing_position = await self._global_position_sample("landing position")
            home_error_m = horizontal_distance_m(home, landing_position)
            if home_error_m > mission.home_tolerance_m:
                raise RuntimeError(
                    f"RTL landed {home_error_m:.1f} m from home; limit is {mission.home_tolerance_m:.1f} m."
                )
        except Exception:
            if not airborne:
                execution = execution.transition(MissionPhase.LANDING).transition(MissionPhase.FAILED)
                raise
            await self._fallback_land_after_airborne_failure(
                execution,
                airborne,
                min(mission.landing_timeout_s, self._runtime_policy.landing_confirmation_timeout_s),
            )
            raise
        return execution.transition(MissionPhase.COMPLETED)

    async def _require_preflight(self) -> None:
        """Verify PX4 telemetry before issuing any configuration or flight command."""
        health = await self._first_telemetry_sample(self._drone.telemetry.health(), "health")
        if not self._has_required_navigation_health(health):
            raise MissionPreflightError(
                "Preflight rejected: health indicates global position or home position is not ready."
            )

        home = await self._first_telemetry_sample(self._drone.telemetry.home(), "home")
        self._require_global_position(home, "home")

        position = await self._first_telemetry_sample(
            self._drone.telemetry.position(), "global position"
        )
        self._require_global_position(position, "global position")

        battery = await self._first_telemetry_sample(self._drone.telemetry.battery(), "battery")
        try:
            remaining_percent = self._battery_percent(battery)
        except MissionPreflightError:
            if self._safety_profile.allow_missing_battery_telemetry:
                return
            raise
        if remaining_percent < self._safety_profile.minimum_battery_percent_to_start:
            required = self._safety_profile.minimum_battery_percent_to_start
            raise MissionPreflightError(
                f"Preflight rejected: battery is {remaining_percent:.1f}%, below the required {required:.1f}%."
            )

    async def _global_position_sample(self, label: str) -> GlobalPosition:
        sample = await self._first_telemetry_sample(self._drone.telemetry.position(), label)
        self._require_global_position(sample, label)
        return GlobalPosition(sample.latitude_deg, sample.longitude_deg, sample.absolute_altitude_m)

    @staticmethod
    async def _first_telemetry_sample(stream: AsyncIterator[object], label: str) -> object:
        try:
            return await anext(stream)
        except StopAsyncIteration as error:
            raise MissionPreflightError(
                f"Preflight rejected: {label} telemetry is unavailable."
            ) from error

    @staticmethod
    def _has_required_navigation_health(health: object) -> bool:
        """Require navigation readiness, but not unrelated GCS health checks."""
        try:
            return bool(health.is_global_position_ok and health.is_home_position_ok)
        except AttributeError as error:
            raise MissionPreflightError("Preflight rejected: health telemetry is unavailable.") from error

    @staticmethod
    def _require_global_position(position: object, label: str) -> None:
        try:
            latitude = float(getattr(position, "latitude_deg"))
            longitude = float(getattr(position, "longitude_deg"))
            altitude = float(getattr(position, "absolute_altitude_m"))
        except (AttributeError, TypeError, ValueError) as error:
            raise MissionPreflightError(f"Preflight rejected: {label} telemetry is unavailable.") from error
        if (
            not all(isfinite(value) for value in (latitude, longitude, altitude))
            or not -90.0 <= latitude <= 90.0
            or not -180.0 <= longitude <= 180.0
        ):
            raise MissionPreflightError(f"Preflight rejected: {label} telemetry is invalid.")

    @staticmethod
    def _battery_percent(battery: object) -> float:
        try:
            remaining = float(getattr(battery, "remaining_percent"))
        except (AttributeError, TypeError, ValueError) as error:
            raise MissionPreflightError("Preflight rejected: battery telemetry is unavailable.") from error
        if not isfinite(remaining) or not 0.0 <= remaining <= 1.0:
            raise MissionPreflightError("Preflight rejected: battery telemetry is invalid.")
        return remaining * 100.0

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
