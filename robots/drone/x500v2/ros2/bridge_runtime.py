"""Runtime lifecycle for the optional, telemetry-only X500 V2 ROS bridge.

This module defines narrow structural boundaries so its lifecycle can be
tested without ROS 2 or MAVSDK.  The production factory remains lazy-imported
in the CLI module; this runtime never invokes PX4 flight-control APIs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Protocol

from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay, TelemetryVehicle
from brain.telemetry.ros2_contract import load_ros2_telemetry_bridge_contract


class RosClient(Protocol):
    """Minimal ROS context lifecycle required by this publishing process."""

    def init(self, *, args: object = None) -> None: ...

    def shutdown(self) -> None: ...


class TelemetryNode(Protocol):
    """The bridge can publish domain telemetry and destroy its node only."""

    def publish(self, event: object) -> None: ...

    def destroy_node(self) -> None: ...


class MavsdkCore(Protocol):
    """Link state as MAVSDK actually exposes it: under ``System.core``.

    Naming this separately is not ceremony. The first real run against MAVSDK
    found the bridge calling ``connection_state()`` straight on the system, which
    the library has never offered; the fake collaborator answered it happily, so
    every test passed while the only code path that talks to a real vehicle could
    not work. The protocol now mirrors the library's shape so a fake cannot drift
    away from it again.
    """

    def connection_state(self): ...


class ConnectableTelemetryVehicle(TelemetryVehicle, Protocol):
    """Read-only MAVSDK connection boundary; no action API is admitted."""

    async def connect(self, *, system_address: str) -> None: ...

    @property
    def core(self) -> MavsdkCore: ...


def route_declared_topics_to(publish: Callable[[Any], None]) -> Callable[[Any], None]:
    """Send ROS exactly the topics the contract declares, and nothing else.

    The relay feeds every stream it collects to one callback, and it has grown
    well past this bridge: attitude, IMU, GNSS detail, NED velocity, landed state
    and battery diagnostics all arrive here too, because the dashboard and the
    stored history want them. The ROS contract deliberately declares three
    topics, so the publisher rejects the rest - correctly, but that rejection
    used to travel up through the relay and kill the whole bridge on its first
    real vehicle. Forwarding indiscriminately was the bug; the contract was right.

    Nothing is discarded here. Every event still reaches the dashboard snapshot,
    which the relay writes independently of this callback. This is routing, not
    filtering away. Widening what ROS sees is a versioned contract change with
    its own safety review, never a change to this function.
    """
    declared = frozenset(topic.name for topic in load_ros2_telemetry_bridge_contract().topics)

    def route(event: Any) -> None:
        if getattr(event, "topic", None) in declared:
            publish(event)

    return route


class TelemetryBridgeRuntime:
    """Own one ROS context and relay MAVSDK telemetry until asked to stop."""

    def __init__(
        self,
        *,
        vehicle: ConnectableTelemetryVehicle,
        ros_client: RosClient,
        node_factory: Callable[[], TelemetryNode],
        destination: Path,
        endpoint: str,
        connection_timeout: float = 15.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._vehicle = vehicle
        self._ros_client = ros_client
        self._node_factory = node_factory
        self._destination = destination
        self._endpoint = endpoint
        if not math.isfinite(connection_timeout) or connection_timeout <= 0:
            raise ValueError("connection_timeout must be a positive, finite number.")
        self._connection_timeout = connection_timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(self, stop_event: asyncio.Event) -> None:
        """Connect, publish read-only telemetry, then deterministically clean up."""
        initialized = False
        node: TelemetryNode | None = None
        try:
            self._ros_client.init(args=None)
            initialized = True
            node = self._node_factory()
            if not await self._connect(stop_event):
                return
            relay = MavsdkTelemetryRelay(
                self._vehicle,
                self._destination,
                clock=self._clock,
                on_event=route_declared_topics_to(node.publish),
            )
            await relay.run(stop_event)
        finally:
            try:
                if node is not None:
                    node.destroy_node()
            finally:
                if initialized:
                    self._ros_client.shutdown()

    async def _connect(self, stop_event: asyncio.Event) -> bool:
        """Discover PX4 within a bounded interval or return promptly on shutdown."""
        discovery = asyncio.create_task(self._discover_px4())
        stopped = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                (discovery, stopped),
                timeout=self._connection_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if discovery in done:
                await discovery
                return True
            if stopped in done:
                return False
            raise TimeoutError(
                f"Timed out waiting for PX4 discovery after {self._connection_timeout:g} seconds."
            )
        finally:
            for task in (discovery, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(discovery, stopped, return_exceptions=True)

    async def _discover_px4(self) -> None:
        await self._vehicle.connect(system_address=self._endpoint)
        async for state in self._vehicle.core.connection_state():
            if state.is_connected:
                return
        raise RuntimeError("MAVSDK connection state ended before PX4 was discovered.")
