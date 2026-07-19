import asyncio
from dataclasses import replace
import unittest

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter, MissionPreflightError
from brain.safety.profile import SafetyProfile, load_safety_profile
from brain.mission.runtime_policy import RuntimePolicy
from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.mission.execution import MissionPhase
from brain.mission.flight import (
    TakeoffHoverLandMission,
    TakeoffReturnToHomeMission,
    TakeoffTargetApproachLandMission,
    TakeoffWaypointLandMission,
)


class ConnectedState:
    is_connected = True


class FakeCore:
    async def connection_state(self):
        yield ConnectedState()


class FakeAction:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def set_takeoff_altitude(self, altitude_m: float) -> None:
        self._events.append(("set_takeoff_altitude", altitude_m))

    async def set_return_to_launch_altitude(self, altitude_m: float) -> None:
        self._events.append(("set_return_to_launch_altitude", altitude_m))

    async def arm(self) -> None:
        self._events.append("arm")

    async def takeoff(self) -> None:
        self._events.append("takeoff")

    async def land(self) -> None:
        self._events.append("land")

    async def return_to_launch(self) -> None:
        self._events.append("return_to_launch")

    async def goto_location(self, latitude: float, longitude: float, altitude: float, yaw: float) -> None:
        self._events.append(("goto_location", latitude, longitude, altitude, yaw))


class Position:
    def __init__(self, latitude: float = 47.5, longitude: float = 19.1, altitude: float = 120.0) -> None:
        self.latitude_deg = latitude
        self.longitude_deg = longitude
        self.absolute_altitude_m = altitude
        self.relative_altitude_m = 2.0


class FakeTelemetry:
    def __init__(self) -> None:
        self._calls = 0

    async def position(self):
        self._calls += 1
        if self._calls <= 2:
            while True:
                yield Position()
                await asyncio.sleep(0)
        else:
            while True:
                yield Position(latitude=47.5000449)
                await asyncio.sleep(0)

    async def in_air(self):
        yield True
        yield False

    async def health(self):
        yield Health(global_position_ok=True, home_position_ok=True)

    async def home(self):
        yield Position()

    async def battery(self):
        while True:
            yield Battery(remaining_percent=75.0)
            await asyncio.sleep(0)


class Battery:
    def __init__(self, remaining_percent: float) -> None:
        self.remaining_percent = remaining_percent


class Health:
    def __init__(self, global_position_ok: bool, home_position_ok: bool) -> None:
        self.is_global_position_ok = global_position_ok
        self.is_home_position_ok = home_position_ok


class FakeDrone:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.action = FakeAction(self.events)
        self.core = FakeCore()
        self.telemetry = FakeTelemetry()

    async def connect(self, system_address: str) -> None:
        self.events.append(("connect", system_address))


class NeverLandingTelemetry(FakeTelemetry):
    async def in_air(self):
        while True:
            yield True
            await asyncio.sleep(0.001)


class NeverLandingDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = NeverLandingTelemetry()


class FailingWaypointAction(FakeAction):
    async def goto_location(self, latitude: float, longitude: float, altitude: float, yaw: float) -> None:
        self._events.append(("goto_location", latitude, longitude, altitude, yaw))
        raise RuntimeError("navigation command rejected")


class FailingWaypointDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.action = FailingWaypointAction(self.events)


class UnhealthyTelemetry(FakeTelemetry):
    async def health(self):
        yield Health(global_position_ok=False, home_position_ok=True)

    async def home(self):
        yield Position(latitude=float("nan"))


class SilentHealthTelemetry(FakeTelemetry):
    async def health(self):
        await asyncio.sleep(60)
        if False:
            yield Health(global_position_ok=True, home_position_ok=True)


class SilentHealthDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = SilentHealthTelemetry()


class UnhealthyDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = UnhealthyTelemetry()


class BecomingHealthyTelemetry(FakeTelemetry):
    def __init__(self) -> None:
        super().__init__()
        self.health_calls = 0

    async def health(self):
        self.health_calls += 1
        yield Health(global_position_ok=False, home_position_ok=False)
        yield Health(global_position_ok=True, home_position_ok=True)


class BecomingHealthyDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = BecomingHealthyTelemetry()


class MissingHomeTelemetry(FakeTelemetry):
    async def home(self):
        if False:
            yield Position()


class MissingHomeDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = MissingHomeTelemetry()


class GnssDropoutTelemetry(FakeTelemetry):
    """Reports a valid preflight fix, then loses the position estimate in flight."""

    async def position(self):
        self._calls += 1
        if self._calls == 1:
            yield Position()
        else:
            yield Position(latitude=float("nan"))


class GnssDropoutDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = GnssDropoutTelemetry()


class RuntimeLowBatteryTelemetry(FakeTelemetry):
    def __init__(self) -> None:
        super().__init__()
        self._battery_calls = 0

    async def battery(self):
        self._battery_calls += 1
        if self._battery_calls == 1:
            yield Battery(remaining_percent=75.0)
            return
        while True:
            yield Battery(remaining_percent=34.0)
            await asyncio.sleep(0)

    async def position(self):
        while True:
            yield Position()
            await asyncio.sleep(0)


class RuntimeLowBatteryDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = RuntimeLowBatteryTelemetry()


class RuntimeTelemetryDropoutTelemetry(FakeTelemetry):
    def __init__(self) -> None:
        super().__init__()
        self._battery_calls = 0

    async def battery(self):
        self._battery_calls += 1
        if self._battery_calls == 1:
            yield Battery(remaining_percent=75.0)
        return

    async def position(self):
        while True:
            yield Position()
            await asyncio.sleep(0)


class RuntimeTelemetryDropoutDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = RuntimeTelemetryDropoutTelemetry()


class UnknownBatteryThenZeroTelemetry(FakeTelemetry):
    """Models an unreadable preflight battery followed by a zero sample."""

    def __init__(self) -> None:
        super().__init__()
        self._battery_calls = 0

    async def battery(self):
        self._battery_calls += 1
        if self._battery_calls == 1:
            yield Battery(remaining_percent=float("nan"))
            return
        while True:
            yield Battery(remaining_percent=0.0)
            await asyncio.sleep(0)

    async def position(self):
        while True:
            yield Position()
            await asyncio.sleep(0)


class UnknownBatteryThenZeroDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = UnknownBatteryThenZeroTelemetry()


class FailingArmAction(FakeAction):
    async def arm(self) -> None:
        self._events.append("arm")
        raise RuntimeError("arm command rejected")


class FailingArmDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.action = FailingArmAction(self.events)


class MavsdkMissionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_battery_fails_closed_before_arming(self) -> None:
        """The X500 profile no longer permits an unknown battery: PX4 SITL does model one."""
        drone = UnknownBatteryThenZeroDrone()
        adapter = MavsdkMissionAdapter(drone, sleep=asyncio.sleep)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.01)

        with self.assertRaisesRegex(RuntimeError, "battery telemetry is invalid"):
            await adapter.execute(mission)

        self.assertNotIn("arm", drone.events)

    async def test_a_profile_that_permits_an_unknown_battery_does_not_read_zero_as_low(self) -> None:
        """The escape hatch survives for profiles whose battery really is unmodelled."""
        drone = UnknownBatteryThenZeroDrone()
        adapter = MavsdkMissionAdapter(
            drone,
            sleep=asyncio.sleep,
            safety_profile=replace(load_safety_profile(), allow_missing_battery_telemetry=True),
        )
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.01)

        await adapter.execute(mission)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_lands_once_when_live_battery_crosses_the_runtime_reserve(self) -> None:
        drone = RuntimeLowBatteryDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(RuntimeError, "low_battery"):
            await adapter.execute(mission)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_lands_once_when_live_battery_telemetry_stops(self) -> None:
        drone = RuntimeTelemetryDropoutDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(RuntimeError, "telemetry_unavailable"):
            await adapter.execute(mission)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_waits_on_one_health_stream_until_navigation_is_ready(self) -> None:
        drone = BecomingHealthyDrone()
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=1.0)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.0)

        await adapter.execute(mission)

        self.assertEqual(drone.events[0], ("set_takeoff_altitude", 2.0))
        self.assertEqual(drone.telemetry.health_calls, 1)

    async def test_rejects_unhealthy_vehicle_before_any_action(self) -> None:
        drone = UnhealthyDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(MissionPreflightError, "navigation health"):
            await adapter.execute(mission)

        self.assertEqual(drone.events, [])

    async def test_rejects_unhealthy_navigation_even_when_position_values_are_finite(self) -> None:
        drone = FakeDrone()

        async def unhealthy_health():
            yield Health(global_position_ok=False, home_position_ok=True)

        drone.telemetry.health = unhealthy_health
        adapter = MavsdkMissionAdapter(drone)

        with self.assertRaisesRegex(MissionPreflightError, "navigation health"):
            await adapter.verify_preflight()

        self.assertEqual(drone.events, [])

    async def test_uses_current_home_and_position_when_health_stream_is_silent(self) -> None:
        drone = SilentHealthDrone()
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.01, sleep=asyncio.sleep)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.0)

        await adapter.execute(mission)

        self.assertIn("arm", drone.events)

    async def test_rejects_battery_below_the_safety_profile_before_any_action(self) -> None:
        drone = FakeDrone()
        profile = SafetyProfile(
            vehicle_id="test",
            max_altitude_m=20.0,
            max_speed_m_s=3.0,
            max_radius_m=50.0,
            minimum_battery_percent_to_start=80.0,
            loss_of_link_action="RTL",
        )
        adapter = MavsdkMissionAdapter(drone, safety_profile=profile)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(MissionPreflightError, "battery"):
            await adapter.execute(mission)

        self.assertEqual(drone.events, [])

    async def test_rejects_when_home_telemetry_is_unavailable_before_any_action(self) -> None:
        drone = MissingHomeDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(MissionPreflightError, "home"):
            await adapter.execute(mission)

        self.assertEqual(drone.events, [])

    async def test_runs_telemetry_preflight_before_the_first_action(self) -> None:
        drone = FakeDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        await adapter.execute(mission)

        self.assertEqual(drone.events[0], ("set_takeoff_altitude", 2.0))
        self.assertIsNotNone(adapter.preflight_telemetry)
        self.assertEqual(adapter.preflight_telemetry.battery_percent, 75.0)

    async def test_verifies_preflight_without_sending_an_actuation_command(self) -> None:
        drone = FakeDrone()
        adapter = MavsdkMissionAdapter(drone)

        telemetry = await adapter.verify_preflight()

        self.assertEqual(telemetry.battery_percent, 75.0)
        self.assertEqual(drone.events, [])

    async def test_connects_before_executing_the_expected_command_sequence(self) -> None:
        drone = FakeDrone()

        async def fake_sleep(seconds: float) -> None:
            drone.events.append(("hover", seconds))

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=5.0)

        await adapter.connect("udpin://0.0.0.0:14540")
        execution = await adapter.execute(mission)

        self.assertEqual(
            drone.events,
            [
                ("connect", "udpin://0.0.0.0:14540"),
                ("set_takeoff_altitude", 2.0),
                "arm",
                "takeoff",
                ("hover", 5.0),
                "land",
            ],
        )
        self.assertEqual(
            tuple(event.phase for event in execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.HOVERING,
                MissionPhase.LANDING,
                MissionPhase.COMPLETED,
            ),
        )

    async def test_confirms_normal_hover_land_with_telemetry(self) -> None:
        drone = NeverLandingDrone()
        policy = RuntimePolicy("v0.1", waypoint_timeout_s=30.0, landing_confirmation_timeout_s=0.01, fallback_land_attempts=1)
        adapter = MavsdkMissionAdapter(drone, runtime_policy=policy)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.0)

        with self.assertRaises(TimeoutError):
            await adapter.execute(mission)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_target_approach_moves_to_the_proposed_target_then_lands(self) -> None:
        drone = FakeDrone()

        async def fake_sleep(seconds: float) -> None:
            drone.events.append(("wait", seconds))

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        # A wide arrival tolerance decouples this from the fake telemetry's fixed
        # convergence point; the point here is that a proposed move is flown and
        # confirmed, not the exact geometry (that is checked against ground truth).
        mission = TakeoffTargetApproachLandMission(
            takeoff=TakeoffCommand(6.0), hover_duration_s=3.0, capture_settle_seconds=4.0,
            waypoint_tolerance_m=1_000_000.0,
        )

        async def propose_move():
            return WaypointCommand(north_m=2.0, east_m=1.0, target_altitude_m=5.0)

        execution = await adapter.execute_target_approach_mission(mission, propose_move)

        self.assertEqual(drone.events.count("arm"), 1)
        self.assertEqual(drone.events.count("takeoff"), 1)
        self.assertEqual(sum(1 for event in drone.events if event[0] == "goto_location"), 1)
        self.assertEqual(drone.events.count("land"), 1)
        self.assertIn(MissionPhase.NAVIGATING, tuple(event.phase for event in execution.events))
        self.assertEqual(execution.events[-1].phase, MissionPhase.COMPLETED)

    async def test_target_approach_makes_no_move_when_perception_proposes_none(self) -> None:
        drone = FakeDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffTargetApproachLandMission(takeoff=TakeoffCommand(6.0), hover_duration_s=1.0)

        async def propose_no_move():
            return None

        execution = await adapter.execute_target_approach_mission(mission, propose_no_move)

        # No target was proposed, so no navigation command is ever emitted, but the
        # vehicle still lands and completes rather than being left airborne.
        self.assertFalse(any(event[0] == "goto_location" for event in drone.events if isinstance(event, tuple)))
        self.assertEqual(drone.events.count("land"), 1)
        self.assertNotIn(MissionPhase.NAVIGATING, tuple(event.phase for event in execution.events))
        self.assertEqual(execution.events[-1].phase, MissionPhase.COMPLETED)

    async def test_target_approach_lands_once_when_the_proposed_move_fails(self) -> None:
        drone = FailingWaypointDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffTargetApproachLandMission(takeoff=TakeoffCommand(6.0), hover_duration_s=1.0)

        async def propose_move():
            return WaypointCommand(north_m=2.0, east_m=1.0, target_altitude_m=5.0)

        with self.assertRaisesRegex(RuntimeError, "navigation command rejected"):
            await adapter.execute_target_approach_mission(mission, propose_move)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_lands_once_after_an_airborne_waypoint_failure(self) -> None:
        drone = FailingWaypointDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffWaypointLandMission(
            takeoff=TakeoffCommand(2.0),
            waypoint=WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0),
            hover_duration_s=1.0,
        )

        with self.assertRaisesRegex(RuntimeError, "navigation command rejected"):
            await adapter.execute_waypoint_mission(mission)

        self.assertEqual(drone.events.count("land"), 1)

    async def test_retains_the_phases_reached_when_an_airborne_mission_fails(self) -> None:
        """A raising mission returns no execution, so the trail must survive on the adapter."""
        drone = FailingWaypointDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffWaypointLandMission(
            takeoff=TakeoffCommand(2.0),
            waypoint=WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0),
            hover_duration_s=1.0,
        )

        with self.assertRaisesRegex(RuntimeError, "navigation command rejected"):
            await adapter.execute_waypoint_mission(mission)

        self.assertEqual(
            tuple(event.phase for event in adapter.execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.NAVIGATING,
                MissionPhase.LANDING,
                MissionPhase.FAILED,
            ),
        )

    async def test_reports_the_original_failure_when_the_vehicle_never_left_the_ground(self) -> None:
        """A pre-airborne failure records FAILED directly; it must not claim a landing."""
        drone = FailingArmDrone()
        adapter = MavsdkMissionAdapter(drone, sleep=asyncio.sleep)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.01)

        with self.assertRaisesRegex(RuntimeError, "arm command rejected"):
            await adapter.execute(mission)

        self.assertEqual(
            tuple(event.phase for event in adapter.execution.events),
            (MissionPhase.ARMING, MissionPhase.FAILED),
        )
        self.assertNotIn("land", drone.events)

    async def test_lands_once_when_gnss_becomes_invalid_during_waypoint_navigation(self) -> None:
        drone = GnssDropoutDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffWaypointLandMission(
            takeoff=TakeoffCommand(2.0),
            waypoint=WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0),
            hover_duration_s=1.0,
        )

        with self.assertRaisesRegex(RuntimeError, "Runtime position telemetry rejected"):
            await adapter.execute_waypoint_mission(mission)

        self.assertNotIn("goto_location", [event[0] if isinstance(event, tuple) else event for event in drone.events])
        self.assertEqual(drone.events.count("land"), 1)

    async def test_converts_and_sends_an_authorized_relative_waypoint(self) -> None:
        drone = FakeDrone()
        adapter = MavsdkMissionAdapter(drone)

        target = await adapter.goto_relative_waypoint(
            WaypointCommand(north_m=0.0, east_m=10.0, target_altitude_m=3.0)
        )

        self.assertAlmostEqual(target.absolute_altitude_m, 121.0)
        self.assertEqual(drone.events[-1][0], "goto_location")
        self.assertAlmostEqual(drone.events[-1][3], 121.0)

    async def test_executes_takeoff_waypoint_hover_and_landing(self) -> None:
        drone = FakeDrone()

        async def fake_sleep(seconds: float) -> None:
            drone.events.append(("wait", seconds))

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffWaypointLandMission(
            takeoff=TakeoffCommand(2.0),
            waypoint=WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0),
            hover_duration_s=3.0,
        )

        execution = await adapter.execute_waypoint_mission(mission)

        self.assertEqual(
            tuple(event.phase for event in execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.NAVIGATING,
                MissionPhase.HOVERING,
                MissionPhase.LANDING,
                MissionPhase.COMPLETED,
            ),
        )
        self.assertEqual(drone.events[0:3], [("set_takeoff_altitude", 2.0), "arm", "takeoff"])
        self.assertEqual(drone.events[-1], "land")

    async def test_executes_return_to_home_and_confirms_landing(self) -> None:
        drone = FakeDrone()

        async def fake_sleep(seconds: float) -> None:
            drone.events.append(("wait", seconds))

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffReturnToHomeMission(
            takeoff=TakeoffCommand(2.0), hover_duration_s=3.0
        )

        execution = await adapter.execute_return_to_home_mission(mission)

        self.assertEqual(
            tuple(event.phase for event in execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.HOVERING,
                MissionPhase.RETURNING,
                MissionPhase.COMPLETED,
            ),
        )
        self.assertIn("return_to_launch", drone.events)
        self.assertNotIn("land", drone.events)
        self.assertIn(("set_return_to_launch_altitude", 2.0), drone.events)

    async def test_lands_as_a_fallback_when_return_to_home_times_out(self) -> None:
        drone = NeverLandingDrone()

        async def fake_sleep(_seconds: float) -> None:
            return None

        adapter = MavsdkMissionAdapter(drone, sleep=fake_sleep)
        mission = TakeoffReturnToHomeMission(
            takeoff=TakeoffCommand(2.0),
            hover_duration_s=1.0,
            landing_timeout_s=0.01,
        )

        with self.assertRaises(TimeoutError):
            await adapter.execute_return_to_home_mission(mission)

        self.assertIn("return_to_launch", drone.events)
        self.assertEqual(drone.events[-1], "land")
