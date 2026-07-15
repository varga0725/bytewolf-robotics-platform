import unittest

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.mission.execution import MissionPhase
from brain.mission.flight import TakeoffHoverLandMission


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

    async def arm(self) -> None:
        self._events.append("arm")

    async def takeoff(self) -> None:
        self._events.append("takeoff")

    async def land(self) -> None:
        self._events.append("land")

    async def goto_location(self, latitude: float, longitude: float, altitude: float, yaw: float) -> None:
        self._events.append(("goto_location", latitude, longitude, altitude, yaw))


class Position:
    latitude_deg = 47.5
    longitude_deg = 19.1
    absolute_altitude_m = 120.0
    relative_altitude_m = 2.0


class FakeTelemetry:
    async def position(self):
        yield Position()


class FakeDrone:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.action = FakeAction(self.events)
        self.core = FakeCore()
        self.telemetry = FakeTelemetry()

    async def connect(self, system_address: str) -> None:
        self.events.append(("connect", system_address))


class MavsdkMissionAdapterTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_converts_and_sends_an_authorized_relative_waypoint(self) -> None:
        drone = FakeDrone()
        adapter = MavsdkMissionAdapter(drone)

        target = await adapter.goto_relative_waypoint(
            WaypointCommand(north_m=0.0, east_m=10.0, target_altitude_m=3.0)
        )

        self.assertAlmostEqual(target.absolute_altitude_m, 121.0)
        self.assertEqual(drone.events[-1][0], "goto_location")
        self.assertAlmostEqual(drone.events[-1][3], 121.0)
