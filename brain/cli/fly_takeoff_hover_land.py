"""Run a bounded takeoff, hover, and landing mission against PX4 SITL."""

import argparse
import asyncio
from collections.abc import Sequence

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.flight import authorize_takeoff_hover_land
from brain.safety.gate import FlightLimits, SafetyGate


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an approved takeoff, hover, and landing mission on PX4 SITL."
    )
    parser.add_argument("--altitude", type=float, default=2.0, help="Takeoff altitude in metres.")
    parser.add_argument(
        "--hover-seconds", type=float, default=5.0, help="Hover duration in seconds."
    )
    parser.add_argument(
        "--max-altitude", type=float, default=20.0, help="Safety ceiling in metres."
    )
    parser.add_argument(
        "--endpoint",
        default="udpin://0.0.0.0:14540",
        help="PX4 MAVLink endpoint exposed by SITL.",
    )
    parser.add_argument(
        "--connection-timeout",
        type=float,
        default=15.0,
        help="Maximum seconds to wait for the PX4 vehicle to be discovered.",
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    try:
        from mavsdk import System
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt"
        ) from error

    gate = SafetyGate(
        FlightLimits(max_altitude_m=arguments.max_altitude, max_distance_m=500.0)
    )
    mission = authorize_takeoff_hover_land(gate, arguments.altitude, arguments.hover_seconds)
    adapter = MavsdkMissionAdapter(System())

    print(f"Connecting to PX4 at {arguments.endpoint}...")
    await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
    print(
        f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, "
        f"hover for {mission.hover_duration_s:g} s, then land."
    )
    execution = await adapter.execute(mission)
    print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
