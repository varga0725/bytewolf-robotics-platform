"""Lifecycle tests for the optional telemetry-only ROS 2 bridge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from robots.drone.x500v2.ros2.bridge_runtime import TelemetryBridgeRuntime


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


class Vehicle:
    telemetry = Telemetry()

    def __init__(self) -> None:
        self.connected_to: str | None = None

    async def connect(self, *, system_address: str) -> None:
        self.connected_to = system_address

    async def connection_state(self):
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
            for _ in range(20):
                if len(node.events) == 3:
                    break
                await asyncio.sleep(0)
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
            async def connection_state(self):
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
            async def connection_state(self):
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


if __name__ == "__main__":
    unittest.main()
