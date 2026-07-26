"""MAVSDK adapter for executing an approved, bounded mission on PX4."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol

from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.artifacts import MissionTelemetrySnapshot
from brain.mission.flight import (
    InterruptionAction,
    TakeoffHoverLandMission,
    TakeoffInterruptLandMission,
    TakeoffReturnToHomeMission,
    TakeoffTargetApproachLandMission,
    TakeoffWaypointLandMission,
    TakeoffWaypointsLandMission,
    TakeoffWaypointsReturnToHomeMission,
    TakeoffWaypointSquareLandMission,
)
from brain.mission.runtime_policy import RuntimePolicy, load_runtime_policy
from brain.mission.runtime_watchdog import RuntimeTelemetryWatchdog
from brain.navigation.waypoints import (
    GlobalPosition,
    horizontal_distance_m,
    relative_waypoint_to_global,
)
from brain.safety.profile import SafetyProfile, load_safety_profile


class MissionPreflightError(RuntimeError):
    """Raised when required PX4 telemetry cannot authorize a mission start."""


class RuntimeSafetyError(RuntimeError):
    """Raised when live telemetry requires the bounded airborne landing fallback."""


class MavsdkAction(Protocol):
    async def set_takeoff_altitude(self, altitude_m: float) -> None: ...
    async def set_return_to_launch_altitude(self, altitude_m: float) -> None: ...
    async def arm(self) -> None: ...
    async def takeoff(self) -> None: ...
    async def hold(self) -> None: ...
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
        preflight_wait_s: float = 0.0,
    ) -> None:
        self._drone = drone
        self._sleep = sleep
        self._runtime_policy = runtime_policy or load_runtime_policy()
        self._safety_profile = safety_profile or load_safety_profile()
        self._preflight_wait_s = preflight_wait_s
        self._preflight_telemetry: MissionTelemetrySnapshot | None = None
        self._execution = MissionExecution.empty()
        self._runtime_watchdog = RuntimeTelemetryWatchdog(
            minimum_battery_percent=self._runtime_policy.minimum_battery_percent_to_continue,
            telemetry_sample_timeout_s=self._runtime_policy.telemetry_sample_timeout_s,
        )

    @property
    def preflight_telemetry(self) -> MissionTelemetrySnapshot | None:
        """The immutable evidence captured immediately before mission authorization."""
        return self._preflight_telemetry

    @property
    def execution(self) -> MissionExecution:
        """The phases reached so far, readable after a mission raises instead of returning."""
        return self._execution

    def _begin_execution(self) -> MissionExecution:
        return self._record(MissionExecution.empty(), MissionPhase.ARMING)

    def _record(self, execution: MissionExecution, phase: MissionPhase) -> MissionExecution:
        """Transition and retain the trail, so a failure boundary keeps the phases reached."""
        self._execution = execution.transition(phase)
        return self._execution

    async def connect(self, system_address: str) -> None:
        await self._drone.connect(system_address=system_address)
        async for state in self._drone.core.connection_state():
            if state.is_connected:
                return

    async def verify_preflight(self) -> MissionTelemetrySnapshot:
        """Capture pre-arm telemetry evidence without issuing a flight command."""
        await self._require_preflight()
        if self._preflight_telemetry is None:
            raise AssertionError("A successful preflight check must capture telemetry evidence.")
        return self._preflight_telemetry

    async def execute(self, mission: TakeoffHoverLandMission) -> MissionExecution:
        """Execute takeoff-hover-land and confirm the normal landing by telemetry."""
        await self._require_preflight()
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def execute_controlled_interruption_mission(
        self, mission: TakeoffInterruptLandMission
    ) -> MissionExecution:
        """Exercise an explicit HOLD or LAND interruption with a safe terminal state.

        The command sequence is deliberately small and audit-visible.  HOLD is
        not a terminal state in this harness: after its bounded observation
        interval the adapter sends exactly one normal LAND command.
        """
        await self._require_preflight()
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.interrupt_after_s)
            if mission.interruption_action is InterruptionAction.HOLD:
                await self._drone.action.hold()
                execution = self._record(execution, MissionPhase.HOLDING)
                await self._sleep_with_runtime_watchdog(mission.hold_cleanup_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def goto_relative_waypoint(self, command: WaypointCommand) -> GlobalPosition:
        position = await anext(self._drone.telemetry.position())
        self._require_runtime_global_position(position, "waypoint origin")
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
                self._require_runtime_global_position(position, "waypoint progress")
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
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep_with_runtime_watchdog(mission.takeoff_settle_seconds)
            execution = self._record(execution, MissionPhase.NAVIGATING)
            target = await self.goto_relative_waypoint(mission.waypoint)
            await self.wait_until_waypoint_reached(
                target,
                mission.waypoint_tolerance_m,
                min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s),
            )
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def execute_target_approach_mission(
        self,
        mission: TakeoffTargetApproachLandMission,
        propose_move: Callable[[], Awaitable[WaypointCommand | None]],
    ) -> MissionExecution:
        """Take off, ask perception for a move, and visit it only if one is proposed.

        ``propose_move`` is the perception decision, called once the vehicle is
        airborne and settled. It returns a waypoint the SafetyGate has already
        approved, or ``None`` to make no move -- a target that was not seen, was
        too uncertain, or that the gate refused. This adapter never proposes a
        move of its own: it flies exactly what perception hands it, or nothing,
        and lands either way. The single airborne-land fallback still covers any
        failure after takeoff, as in every other mission here.
        """
        await self._require_preflight()
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep_with_runtime_watchdog(mission.capture_settle_seconds)
            waypoint = await propose_move()
            if waypoint is not None:
                execution = self._record(execution, MissionPhase.NAVIGATING)
                target = await self.goto_relative_waypoint(waypoint)
                await self.wait_until_waypoint_reached(
                    target,
                    mission.waypoint_tolerance_m,
                    min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s),
                )
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(
                execution, airborne, self._runtime_policy.landing_confirmation_timeout_s
            )
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def execute_waypoints_mission(self, mission: TakeoffWaypointsLandMission) -> MissionExecution:
        """Visit every approved local route point; one failure lands immediately."""
        await self._require_preflight()
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm(); execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff(); airborne = True
            await self._sleep_with_runtime_watchdog(mission.takeoff_settle_seconds)
            execution = self._record(execution, MissionPhase.NAVIGATING)
            timeout_s = min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s)
            for waypoint in mission.waypoints:
                target = await self.goto_relative_waypoint(waypoint)
                await self.wait_until_waypoint_reached(target, mission.waypoint_tolerance_m, timeout_s)
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
        except Exception:
            await self._fallback_land_after_airborne_failure(execution, airborne, self._runtime_policy.landing_confirmation_timeout_s)
            raise
        return await self._normal_land(execution, self._runtime_policy.landing_confirmation_timeout_s)

    async def execute_waypoint_square_mission(
        self, mission: TakeoffWaypointSquareLandMission
    ) -> MissionExecution:
        """Visit four pre-authorized corners sequentially, confirming each before landing.

        There is intentionally no retry or skip path: any navigation or arrival
        failure causes one bounded landing fallback and propagates the error.
        """
        await self._require_preflight()
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep_with_runtime_watchdog(mission.takeoff_settle_seconds)
            execution = self._record(execution, MissionPhase.NAVIGATING)
            timeout_s = min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s)
            for waypoint in mission.waypoints:
                target = await self.goto_relative_waypoint(waypoint)
                await self.wait_until_waypoint_reached(
                    target, mission.waypoint_tolerance_m, timeout_s
                )
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
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
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        await self._drone.action.set_return_to_launch_altitude(mission.return_to_home.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep_with_runtime_watchdog(mission.takeoff_settle_seconds)
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
            execution = self._record(execution, MissionPhase.RETURNING)
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
            await self._fallback_land_after_airborne_failure(
                execution,
                airborne,
                min(mission.landing_timeout_s, self._runtime_policy.landing_confirmation_timeout_s),
            )
            raise
        return self._record(execution, MissionPhase.COMPLETED)

    async def execute_waypoints_return_to_home_mission(
        self, mission: TakeoffWaypointsReturnToHomeMission
    ) -> MissionExecution:
        """Fly every approved route point, then let PX4 bring the vehicle home.

        This is `execute_waypoints_mission` and `execute_return_to_home_mission`
        joined at the point where they differ, and nowhere else: the same
        per-waypoint arrival confirmation, the same single land fallback, and
        the same landed-at-home tolerance check that refuses to call a return
        successful because the vehicle merely stopped moving.

        There is no retry and no skip. A waypoint that cannot be reached is one
        bounded landing and a raised error, exactly as on the landing route.
        """
        await self._require_preflight()
        home = await self._global_position_sample("home")
        execution = self._begin_execution()
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        await self._drone.action.set_return_to_launch_altitude(mission.return_to_home.target_altitude_m)
        airborne = False
        try:
            await self._drone.action.arm()
            execution = self._record(execution, MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            airborne = True
            await self._sleep_with_runtime_watchdog(mission.takeoff_settle_seconds)
            execution = self._record(execution, MissionPhase.NAVIGATING)
            timeout_s = min(mission.waypoint_timeout_s, self._runtime_policy.waypoint_timeout_s)
            for waypoint in mission.waypoints:
                target = await self.goto_relative_waypoint(waypoint)
                await self.wait_until_waypoint_reached(target, mission.waypoint_tolerance_m, timeout_s)
            execution = self._record(execution, MissionPhase.HOVERING)
            await self._sleep_with_runtime_watchdog(mission.hover_duration_s)
            execution = self._record(execution, MissionPhase.RETURNING)
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
            await self._fallback_land_after_airborne_failure(
                execution,
                airborne,
                min(mission.landing_timeout_s, self._runtime_policy.landing_confirmation_timeout_s),
            )
            raise
        return self._record(execution, MissionPhase.COMPLETED)

    async def _require_preflight(self) -> None:
        """Verify PX4 telemetry before issuing any configuration or flight command."""
        deadline = asyncio.get_running_loop().time() + self._preflight_wait_s
        health_stream = self._drone.telemetry.health()
        # A missing refresh is not alone a reason to reject a vehicle: PX4 can
        # stop publishing Health after it is ready.  A received unhealthy
        # sample is different: issuing arm after it would merely turn a clear
        # preflight failure into a COMMAND_DENIED action failure.
        health = None
        while True:
            remaining = max(deadline - asyncio.get_running_loop().time(), 0.0)
            try:
                health = await asyncio.wait_for(anext(health_stream), timeout=min(5.0, max(remaining, 0.01)))
            except (TimeoutError, StopAsyncIteration):
                break
            if self._has_required_navigation_health(health):
                break
            if remaining <= 0:
                raise MissionPreflightError(
                    "Preflight rejected: the vehicle did not report itself ready to arm "
                    "(global position, home, or PX4's own arming checks)."
                )

        home = await self._first_telemetry_sample(self._drone.telemetry.home(), "home")
        self._require_global_position(home, "home")

        position = await self._first_telemetry_sample(
            self._drone.telemetry.position(), "global position"
        )
        self._require_global_position(position, "global position")

        battery = await self._first_telemetry_sample(self._drone.telemetry.battery(), "battery")
        remaining_percent: float | None
        try:
            remaining_percent = self._battery_percent(battery)
        except MissionPreflightError:
            if self._safety_profile.allow_missing_battery_telemetry:
                remaining_percent = None
            else:
                raise
        self._preflight_telemetry = MissionTelemetrySnapshot(
            captured_at=datetime.now(UTC),
            navigation_ready=True,
            home_position_valid=True,
            global_position_valid=True,
            battery_percent=remaining_percent,
        )
        if (
            remaining_percent is not None
            and remaining_percent < self._safety_profile.minimum_battery_percent_to_start
        ):
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
        """Require navigation readiness and PX4's own verdict on arming.

        Position and home alone are not the same question PX4 asks itself. It
        runs a wider set of checks — calibration, EKF convergence — and until
        they pass it answers an arm command with COMMAND_DENIED. This code
        already said so, in the comment above the wait: arming an unhealthy
        vehicle "would merely turn a clear preflight failure into a
        COMMAND_DENIED action failure". It then waited on the narrower
        condition anyway, so a mission launched moments after PX4 booted did
        exactly that. Two of six scenarios failed that way in the first
        repetition of `p0-repeatability-20260721T103416Z.json`, and none in the
        nine repetitions after it.

        `is_armable` is PX4's answer to the question actually being asked. It is
        a strictly narrower gate than before: a vehicle that would have been
        armed and refused is now waited for, or rejected before the command.
        """
        try:
            navigation_ready = bool(health.is_global_position_ok and health.is_home_position_ok)
        except AttributeError as error:
            raise MissionPreflightError("Preflight rejected: health telemetry is unavailable.") from error
        # Older MAVSDK Health messages have no verdict to offer; their absence
        # must not be read as "not armable" and stall every flight.
        armable = getattr(health, "is_armable", True)
        return navigation_ready and bool(armable)

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

    @classmethod
    def _require_runtime_global_position(cls, position: object, label: str) -> None:
        """Reject invalid in-flight GNSS samples before they can command navigation."""
        try:
            cls._require_global_position(position, label)
        except MissionPreflightError as error:
            message = str(error).replace(
                "Preflight rejected:", "Runtime position telemetry rejected:"
            )
            raise RuntimeError(message) from error

    @staticmethod
    def _battery_percent(battery: object) -> float:
        """Read MAVSDK's battery percentage, which is already a 0-100 value."""
        try:
            remaining = float(getattr(battery, "remaining_percent"))
        except (AttributeError, TypeError, ValueError) as error:
            raise MissionPreflightError("Preflight rejected: battery telemetry is unavailable.") from error
        if not isfinite(remaining) or not 0.0 <= remaining <= 100.0:
            raise MissionPreflightError("Preflight rejected: battery telemetry is invalid.")
        return remaining

    async def _sleep_with_runtime_watchdog(self, duration_s: float) -> None:
        """Observe live battery and GNSS while the controller is intentionally waiting.

        The watchdog runs only inside this still-live MAVSDK process.  If it
        detects a fault, mission execution raises and the existing outer
        boundary sends the single permitted landing fallback.  If this process
        itself stops, PX4's independently configured failsafe remains the
        safety authority; no code here can claim to command after termination.
        """
        if duration_s <= 0.0:
            await self._sleep(duration_s)
            return
        sleep_task = asyncio.create_task(self._sleep(duration_s))
        # Preserve zero-cost injected sleepers used by deterministic unit tests;
        # a completed wait has no in-flight interval that needs monitoring.
        await asyncio.sleep(0)
        if sleep_task.done():
            await sleep_task
            return
        position_task = asyncio.create_task(
            self._watch_runtime_position(self._drone.telemetry.position())
        )
        tasks = {sleep_task, position_task}
        # The X500 SITL profile explicitly allows an unmodelled battery.  When
        # preflight could not establish a trustworthy value, a later PX4 zero
        # sample is still unknown rather than evidence of a real low battery.
        # A physical profile (or any trusted preflight reading) continues to
        # monitor the live battery and lands on reserve violation.
        if not (
            self._safety_profile.allow_missing_battery_telemetry
            and self._preflight_telemetry is not None
            and self._preflight_telemetry.battery_percent is None
        ):
            tasks.add(asyncio.create_task(self._watch_runtime_battery(self._drone.telemetry.battery())))
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if sleep_task in done:
                await sleep_task
                return
            for task in done:
                if task is not sleep_task:
                    await task
            raise AssertionError("Runtime telemetry watcher ended without a safety decision.")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_runtime_battery(self, stream: AsyncIterator[object]) -> None:
        iterator = stream.__aiter__()
        while True:
            sample = await self._next_runtime_sample(iterator, "battery")
            decision = self._runtime_watchdog.evaluate_battery(sample)
            self._require_runtime_decision(decision)

    async def _watch_runtime_position(self, stream: AsyncIterator[object]) -> None:
        iterator = stream.__aiter__()
        while True:
            sample = await self._next_runtime_sample(iterator, "position")
            decision = self._runtime_watchdog.evaluate_position(sample)
            self._require_runtime_decision(decision)

    async def _next_runtime_sample(self, iterator: AsyncIterator[object], source: str) -> object:
        try:
            return await asyncio.wait_for(
                anext(iterator), timeout=self._runtime_watchdog.telemetry_sample_timeout_s
            )
        except (StopAsyncIteration, TimeoutError) as error:
            decision = self._runtime_watchdog.telemetry_unavailable(source)
            self._require_runtime_decision(decision)
            raise AssertionError("Unavailable telemetry must have raised a runtime safety error.") from error

    @staticmethod
    def _require_runtime_decision(decision: object) -> None:
        if getattr(decision, "permitted", False):
            return
        fault = getattr(decision, "fault", None)
        action = getattr(decision, "action", None)
        if fault is None or action is None:
            raise RuntimeSafetyError(
                "Runtime safety fault cannot be handled by this live controller; PX4 failsafe is required."
            )
        raise RuntimeSafetyError(f"Runtime safety fallback requested: {fault.kind.value} from {fault.source}.")

    async def _normal_land(
        self, execution: MissionExecution, confirmation_timeout_s: float
    ) -> MissionExecution:
        """Send the one normal land command and require telemetry confirmation."""
        execution = self._record(execution, MissionPhase.LANDING)
        try:
            await self._drone.action.land()
            await self.wait_until_landed(confirmation_timeout_s, require_airborne_observation=False)
        except Exception:
            execution = self._record(execution, MissionPhase.FAILED)
            raise
        return self._record(execution, MissionPhase.COMPLETED)

    async def _fallback_land_after_airborne_failure(
        self, execution: MissionExecution, airborne: bool, confirmation_timeout_s: float
    ) -> MissionExecution:
        """Use exactly one fallback land after a pre-landing airborne failure."""
        if not airborne:
            return self._record(execution, MissionPhase.FAILED)
        execution = self._record(execution, MissionPhase.LANDING)
        try:
            await self._drone.action.land()
            await self.wait_until_landed(confirmation_timeout_s, require_airborne_observation=False)
        finally:
            execution = self._record(execution, MissionPhase.FAILED)
        return execution
