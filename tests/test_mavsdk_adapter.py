import asyncio
import unittest

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter, MissionPreflightError
from brain.safety.profile import SafetyProfile
from brain.mission.runtime_policy import RuntimePolicy
from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.mission.execution import MissionPhase
from brain.mission.flight import (
    TakeoffHoverLandMission,
    TakeoffReturnToHomeMission,
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
            yield Position()
        else:
            yield Position(latitude=47.5000449)

    async def in_air(self):
        yield True
        yield False

    async def health_all_ok(self):
        yield True

    async def home(self):
        yield Position()

    async def battery(self):
        yield Battery(remaining_percent=0.75)


class Battery:
    def __init__(self, remaining_percent: float) -> None:
        self.remaining_percent = remaining_percent


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
    async def health_all_ok(self):
        yield False


class UnhealthyDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = UnhealthyTelemetry()


class MissingHomeTelemetry(FakeTelemetry):
    async def home(self):
        if False:
            yield Position()


class MissingHomeDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry = MissingHomeTelemetry()


class MavsdkMissionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unhealthy_vehicle_before_any_action(self) -> None:
        drone = UnhealthyDrone()
        adapter = MavsdkMissionAdapter(drone)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=1.0)

        with self.assertRaisesRegex(MissionPreflightError, "health"):
            await adapter.execute(mission)

        self.assertEqual(drone.events, [])

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
