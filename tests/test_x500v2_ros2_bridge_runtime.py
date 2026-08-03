"""Lifecycle tests for the optional telemetry-only ROS 2 bridge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import time
import unittest

from robots.drone.x500v2.ros2.bridge_runtime import (
    TelemetryBridgeRuntime,
    route_declared_topics_to,
)
from robots.drone.x500v2.ros2.telemetry_adapter import declared_telemetry_topics


class Position:
    latitude_deg = 47.4979
    longitude_deg = 19.0402
    absolute_altitude_m = 125.5
    relative_altitude_m = 2.0


class Battery:
    remaining_percent = 0.78


class Telemetry:
    async def _stream(self, value: object):
        while True:
            yield value
            await asyncio.sleep(0)

    def position(self):
        return self._stream(Position())

    def battery(self):
        return self._stream(Battery())

    def in_air(self):
        return self._stream(True)


class _Event:
    """The only thing routing looks at is the topic."""

    def __init__(self, topic: str) -> None:
        self.topic = topic


class VehicleCore:
    """Mirrors MAVSDK's ``System.core``, which is where link state really lives.

    The fake used to answer ``connection_state()`` directly on the vehicle, so
    the suite stayed green while the real bridge could never connect. Keeping the
    nesting here is what makes these tests evidence about MAVSDK rather than
    about themselves.
    """

    def __init__(self, vehicle: "Vehicle") -> None:
        self._vehicle = vehicle

    def connection_state(self):
        return self._vehicle.connection_states()


class Vehicle:
    telemetry = Telemetry()

    def __init__(self) -> None:
        self.connected_to: str | None = None

    @property
    def core(self) -> VehicleCore:
        return VehicleCore(self)

    async def connect(self, *, system_address: str) -> None:
        self.connected_to = system_address

    async def connection_states(self):
        yield type("State", (), {"is_connected": True})()


class RosClient:
    def __init__(self) -> None:
        self.initialized = 0
        self.shutdowns = 0

    def init(self, *, args: object = None) -> None:
        self.initialized += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


class Node:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.destroyed = 0

    def publish(self, event: object) -> None:
        self.events.append(event)

    def destroy_node(self) -> None:
        self.destroyed += 1


class TelemetryBridgeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_then_relays_only_telemetry_and_cleans_up(self) -> None:
        vehicle = Vehicle()
        ros = RosClient()
        node = Node()
        stopped = asyncio.Event()
        with tempfile.TemporaryDirectory() as directory:
            runtime = TelemetryBridgeRuntime(
                vehicle=vehicle,
                ros_client=ros,
                node_factory=lambda: self._node_after_ros_init(ros, node),
                destination=Path(directory) / "telemetry.json",
                endpoint="udpin://0.0.0.0:14540",
                clock=lambda: datetime(2026, 7, 16, 19, 0, tzinfo=UTC),
            )
            task = asyncio.create_task(runtime.run(stopped))
            # Waits on a deadline, not on a number of event-loop turns.
            # asyncio.sleep(0) only yields control; it does not let time pass, so
            # a fixed count of yields is a bet on how many turns the runtime
            # needs, and under scheduling pressure that bet loses.
            deadline = time.monotonic() + 5.0
            while len(node.events) < 3 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            stopped.set()
            await task

            self.assertEqual(vehicle.connected_to, "udpin://0.0.0.0:14540")
            self.assertEqual(ros.initialized, 1)
            self.assertEqual(ros.shutdowns, 1)
            self.assertEqual(node.destroyed, 1)
            self.assertGreaterEqual(len(node.events), 3)
            self.assertEqual(
                {event.topic for event in node.events},
                {
                    "/bytewolf/x500v2_reference_01/telemetry/position",
                    "/bytewolf/x500v2_reference_01/telemetry/battery",
                    "/bytewolf/x500v2_reference_01/telemetry/flight_state",
                },
            )
            self.assertTrue((Path(directory) / "telemetry.json").exists())

    async def test_cleans_up_ros_when_connection_fails_before_relay(self) -> None:
        class FailingVehicle(Vehicle):
            async def connect(self, *, system_address: str) -> None:
                raise RuntimeError("PX4 unavailable")

        ros = RosClient()
        node = Node()
        with tempfile.TemporaryDirectory() as directory:
            runtime = TelemetryBridgeRuntime(
                vehicle=FailingVehicle(),
                ros_client=ros,
                node_factory=lambda: node,
                destination=Path(directory) / "telemetry.json",
                endpoint="udpin://0.0.0.0:14540",
            )
            with self.assertRaisesRegex(RuntimeError, "PX4 unavailable"):
                await runtime.run(asyncio.Event())

        self.assertEqual(ros.shutdowns, 1)
        self.assertEqual(node.destroyed, 1)

    async def test_stop_cancels_never_connected_discovery_and_cleans_up(self) -> None:
        class NeverConnectedVehicle(Vehicle):
            async def connection_states(self):
                while True:
                    await asyncio.sleep(60)
                    yield type("State", (), {"is_connected": False})()

        vehicle = NeverConnectedVehicle()
        ros = RosClient()
        node = Node()
        stopped = asyncio.Event()
        with tempfile.TemporaryDirectory() as directory:
            runtime = TelemetryBridgeRuntime(
                vehicle=vehicle,
                ros_client=ros,
                node_factory=lambda: node,
                destination=Path(directory) / "telemetry.json",
                endpoint="udpin://0.0.0.0:14540",
                connection_timeout=30.0,
            )
            task = asyncio.create_task(runtime.run(stopped))
            await asyncio.sleep(0)
            stopped.set()
            await asyncio.wait_for(task, timeout=0.1)

        self.assertEqual(ros.shutdowns, 1)
        self.assertEqual(node.destroyed, 1)

    async def test_never_connected_discovery_has_a_bounded_timeout(self) -> None:
        class NeverConnectedVehicle(Vehicle):
            async def connection_states(self):
                while True:
                    await asyncio.sleep(60)
                    yield type("State", (), {"is_connected": False})()

        ros = RosClient()
        node = Node()
        with tempfile.TemporaryDirectory() as directory:
            runtime = TelemetryBridgeRuntime(
                vehicle=NeverConnectedVehicle(),
                ros_client=ros,
                node_factory=lambda: node,
                destination=Path(directory) / "telemetry.json",
                endpoint="udpin://0.0.0.0:14540",
                connection_timeout=0.01,
            )
            with self.assertRaisesRegex(TimeoutError, "Timed out waiting for PX4 discovery"):
                await runtime.run(asyncio.Event())

        self.assertEqual(ros.shutdowns, 1)
        self.assertEqual(node.destroyed, 1)

    def _node_after_ros_init(self, ros: RosClient, node: Node) -> Node:
        self.assertEqual(ros.initialized, 1)
        return node


class DeclaredTopicRoutingTests(unittest.TestCase):
    """The relay carries more streams than ROS declares; only the declared ones may cross.

    On the first run against a real vehicle the relay delivered seven extra
    history streams, the publisher rejected the first one exactly as its contract
    says it must, and that rejection killed the entire bridge. The publisher was
    right; the bridge was wrong to hand it everything.
    """

    UNDECLARED = (
        "telemetry/history/imu",
        "telemetry/history/gps_info",
        "telemetry/history/attitude_euler",
        "telemetry/history/velocity_ned",
        "telemetry/history/position_velocity_ned",
        "telemetry/history/landed_state",
        "telemetry/history/battery_diagnostics",
    )

    def setUp(self) -> None:
        self.published: list[object] = []
        self.route = route_declared_topics_to(self.published.append)

    def test_declared_topics_reach_ros(self) -> None:
        for topic in declared_telemetry_topics():
            with self.subTest(topic=topic):
                self.published.clear()
                self.route(_Event(topic))

                self.assertEqual([event.topic for event in self.published], [topic])

    def test_the_relays_history_streams_never_reach_ros(self) -> None:
        for topic in self.UNDECLARED:
            with self.subTest(topic=topic):
                self.published.clear()
                self.route(_Event(topic))

                self.assertEqual(self.published, [], f"{topic} is not in the ROS contract")

    def test_an_undeclared_topic_does_not_raise_and_stop_the_bridge(self) -> None:
        """The whole point: an extra stream must be a non-event, not a crash."""
        def exploding_publish(event: object) -> None:
            raise AssertionError(f"{getattr(event, 'topic', event)!r} must never be published to ROS")

        route = route_declared_topics_to(exploding_publish)

        for topic in self.UNDECLARED:
            with self.subTest(topic=topic):
                route(_Event(topic))


class MavsdkShapeTests(unittest.TestCase):
    """Hold the fake to the library it stands in for.

    Every other test here talks to a fake, which is what keeps them fast and
    simulator-free - but a fake only proves something while it still resembles
    MAVSDK. The bridge shipped for weeks calling ``connection_state()`` on the
    system object, a method the library has never had, and no test could see it
    because the fake offered one. These assertions read the real class, so the
    next such divergence fails here instead of on a live vehicle.
    """

    def setUp(self) -> None:
        try:
            from mavsdk import System
        except ModuleNotFoundError:
            self.skipTest("MAVSDK is not installed in this interpreter.")
        self.system = System

    def test_link_state_lives_under_core_not_on_the_system(self) -> None:
        from mavsdk.core import Core

        self.assertTrue(hasattr(self.system, "core"), "MAVSDK System must expose `core`")
        self.assertTrue(
            hasattr(Core, "connection_state"),
            "MAVSDK Core must expose `connection_state`; the bridge's discovery depends on it",
        )
        self.assertFalse(
            hasattr(self.system, "connection_state"),
            "If MAVSDK ever put `connection_state` on System, this bridge's nesting should be revisited",
        )

    def test_the_system_offers_what_the_bridge_calls_on_it(self) -> None:
        for attribute in ("connect", "core", "telemetry"):
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.system, attribute))


if __name__ == "__main__":
    unittest.main()
