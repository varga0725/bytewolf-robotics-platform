"""Runtime lifecycle for the optional, telemetry-only X500 V2 ROS bridge.

This module defines narrow structural boundaries so its lifecycle can be
tested without ROS 2 or MAVSDK.  The production factory remains lazy-imported
in the CLI module; this runtime never invokes PX4 flight-control APIs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Protocol

from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay, TelemetryVehicle


class RosClient(Protocol):
    """Minimal ROS context lifecycle required by this publishing process."""

    def init(self, *, args: object = None) -> None: ...

    def shutdown(self) -> None: ...


class TelemetryNode(Protocol):
    """The bridge can publish domain telemetry and destroy its node only."""

    def publish(self, event: object) -> None: ...

    def destroy_node(self) -> None: ...


class ConnectableTelemetryVehicle(TelemetryVehicle, Protocol):
    """Read-only MAVSDK connection boundary; no action API is admitted."""

    async def connect(self, *, system_address: str) -> None: ...

    def connection_state(self): ...


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
        self._clock = clock or (lambda: datetime.now(UTC))

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
                on_event=node.publish,
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
        async for state in self._vehicle.connection_state():
            if state.is_connected:
                return
        raise RuntimeError("MAVSDK connection state ended before PX4 was discovered.")
