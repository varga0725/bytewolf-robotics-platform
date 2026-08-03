"""Runtime lifecycle for the optional, telemetry-only X500 V2 ROS bridge.

This module defines narrow structural boundaries so its lifecycle can be
tested without ROS 2 or MAVSDK.  The production factory remains lazy-imported
in the CLI module; this runtime never invokes PX4 flight-control APIs.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Protocol
from uuid import uuid4

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


def _isoformat(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DeclaredTopicRouter:
    """Send ROS exactly the topics the contract declares, and count both sides.

    The tallies are the bridge's own evidence. A run that publishes nothing looks
    identical to a healthy one from the outside - the topics still exist, the
    process still lives - so the counts are what separate "the bridge relayed
    telemetry" from "the bridge started".

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
    its own safety review, never a change to this class.
    """

    def __init__(self, publish: Callable[[Any], None]) -> None:
        self._publish = publish
        self._declared = frozenset(
            topic.name for topic in load_ros2_telemetry_bridge_contract().topics
        )
        self.published: Counter[str] = Counter()
        self.withheld: Counter[str] = Counter()

    @property
    def declared_topics(self) -> tuple[str, ...]:
        return tuple(sorted(self._declared))

    def __call__(self, event: Any) -> None:
        topic = getattr(event, "topic", None)
        if topic in self._declared:
            self.published[topic] += 1
            self._publish(event)
        else:
            self.withheld[str(topic)] += 1


def route_declared_topics_to(publish: Callable[[Any], None]) -> DeclaredTopicRouter:
    """Build the router the bridge wires into the relay."""
    return DeclaredTopicRouter(publish)


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
        artifact_dir: Path | None = None,
    ) -> None:
        self._artifact_dir = artifact_dir
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
        router: DeclaredTopicRouter | None = None
        started_at = self._clock()
        outcome = "failed"
        try:
            self._ros_client.init(args=None)
            initialized = True
            node = self._node_factory()
            if not await self._connect(stop_event):
                outcome = "stopped_before_discovery"
                return
            router = route_declared_topics_to(node.publish)
            relay = MavsdkTelemetryRelay(
                self._vehicle,
                self._destination,
                clock=self._clock,
                on_event=router,
            )
            await relay.run(stop_event)
            outcome = "completed"
        finally:
            self._write_artifact(started_at, outcome, router)
            try:
                if node is not None:
                    node.destroy_node()
            finally:
                if initialized:
                    self._ros_client.shutdown()

    def _write_artifact(
        self, started_at: datetime, outcome: str, router: DeclaredTopicRouter | None
    ) -> None:
        """Record what this bridge run actually relayed, apart from any mission.

        The bridge is a separate process on a separate interpreter from the
        missions, and its evidence stays separate too: a mission artifact says
        what was flown, this says what was observed. Mixing them would let a
        mission run appear to carry telemetry proof it never produced.

        Writing is best-effort by design. A bridge that dies because it could not
        record itself would be a telemetry-only component taking down a flight
        session, which is exactly the coupling this layer must not have.
        """
        if self._artifact_dir is None:
            return
        ended_at = self._clock()
        document = {
            "version": "ros2_telemetry_bridge_run.v1",
            "started_at": _isoformat(started_at),
            "ended_at": _isoformat(ended_at),
            "endpoint": self._endpoint,
            "outcome": outcome,
            "declared_topics": list(router.declared_topics) if router else [],
            "published_per_topic": dict(router.published) if router else {},
            "withheld_per_topic": dict(router.withheld) if router else {},
        }
        try:
            self._artifact_dir.mkdir(parents=True, exist_ok=True)
            stamp = ended_at.strftime("%Y%m%dT%H%M%SZ")
            destination = self._artifact_dir / f"ros2-bridge-{stamp}-{uuid4().hex}.json"
            payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._artifact_dir, prefix=".pending-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(f"{payload}\n")
            temporary.replace(destination)
        except OSError:
            return

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
