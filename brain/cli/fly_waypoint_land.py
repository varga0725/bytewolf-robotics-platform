"""Run a bounded takeoff, relative waypoint, hover, and landing mission."""

import argparse
import asyncio
from collections.abc import Sequence

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.flight import authorize_takeoff_waypoint_land
from brain.safety.gate import FlightLimits, SafetyGate


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe waypoint mission on PX4 SITL.")
    parser.add_argument("--takeoff-altitude", type=float, default=2.0)
    parser.add_argument("--north", type=float, default=5.0, help="Waypoint north offset in metres.")
    parser.add_argument("--east", type=float, default=0.0, help="Waypoint east offset in metres.")
    parser.add_argument("--waypoint-altitude", type=float, default=2.0)
    parser.add_argument("--hover-seconds", type=float, default=3.0)
    parser.add_argument("--max-altitude", type=float, default=20.0)
    parser.add_argument("--max-distance", type=float, default=500.0)
    parser.add_argument("--waypoint-timeout", type=float, default=30.0)
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=15.0)
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    from mavsdk import System

    gate = SafetyGate(
        FlightLimits(
            max_altitude_m=arguments.max_altitude,
            max_distance_m=arguments.max_distance,
        )
    )
    mission = authorize_takeoff_waypoint_land(
        gate,
        takeoff_altitude_m=arguments.takeoff_altitude,
        north_m=arguments.north,
        east_m=arguments.east,
        waypoint_altitude_m=arguments.waypoint_altitude,
        hover_duration_s=arguments.hover_seconds,
        waypoint_timeout_s=arguments.waypoint_timeout,
    )
    adapter = MavsdkMissionAdapter(System())
    print(f"Connecting to PX4 at {arguments.endpoint}...")
    await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
    print(
        f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, move "
        f"{mission.waypoint.north_m:g} m north and {mission.waypoint.east_m:g} m east, then land."
    )
    execution = await adapter.execute_waypoint_mission(mission)
    print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
