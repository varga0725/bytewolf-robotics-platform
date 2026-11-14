"""Sample PX4 position at 1 Hz relative to home for demo evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from math import cos, radians

from brain.navigation.waypoints import GlobalPosition


async def sample_positions_while(
    drone,
    home: GlobalPosition,
    *,
    stop: asyncio.Event,
    interval_s: float = 1.0,
    sink: list[dict[str, object]],
) -> None:
    while not stop.is_set():
        try:
            position = await asyncio.wait_for(anext(drone.telemetry.position()), timeout=interval_s)
            current = GlobalPosition(
                float(position.latitude_deg),
                float(position.longitude_deg),
                float(position.absolute_altitude_m),
            )
            north = radians(current.latitude_deg - home.latitude_deg) * 6_371_000.0
            east = (
                radians(current.longitude_deg - home.longitude_deg)
                * 6_371_000.0
                * cos(radians(home.latitude_deg))
            )
            sink.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "north_m": north,
                    "east_m": east,
                    "relative_altitude_m": current.absolute_altitude_m - home.absolute_altitude_m,
                }
            )
        except (TimeoutError, StopAsyncIteration):
            if stop.is_set():
                return
        await asyncio.sleep(interval_s)
