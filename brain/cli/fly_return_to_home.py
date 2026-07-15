"""Run a bounded takeoff, hover, and PX4 Return-to-Home mission."""

import argparse
import asyncio
from collections.abc import Sequence

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.flight import authorize_takeoff_return_to_home
from brain.safety.gate import FlightLimits, SafetyGate


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe Return-to-Home mission on PX4 SITL.")
    parser.add_argument("--takeoff-altitude", type=float, default=2.0)
    parser.add_argument("--hover-seconds", type=float, default=3.0)
    parser.add_argument("--landing-timeout", type=float, default=60.0)
    parser.add_argument("--max-altitude", type=float, default=20.0)
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=15.0)
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
    mission = authorize_takeoff_return_to_home(
        gate,
        takeoff_altitude_m=arguments.takeoff_altitude,
        hover_duration_s=arguments.hover_seconds,
        landing_timeout_s=arguments.landing_timeout,
    )
    adapter = MavsdkMissionAdapter(System())
    print(f"Connecting to PX4 at {arguments.endpoint}...")
    await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
    print(
        f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, hover for "
        f"{mission.hover_duration_s:g} s, then PX4 returns to launch and lands."
    )
    execution = await adapter.execute_return_to_home_mission(mission)
    print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
