"""Run a bounded takeoff, relative waypoint, hover, and landing mission."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import write_run_artifact
from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from brain.mission.execution import MissionExecution
from brain.mission.flight import authorize_takeoff_waypoint_land
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe waypoint mission on PX4 SITL.")
    parser.add_argument("--takeoff-altitude", type=float, default=2.0)
    parser.add_argument("--north", type=float, default=5.0, help="Waypoint north offset in metres.")
    parser.add_argument("--east", type=float, default=0.0, help="Waypoint east offset in metres.")
    parser.add_argument("--waypoint-altitude", type=float, default=2.0)
    parser.add_argument("--hover-seconds", type=float, default=3.0)
    parser.add_argument(
        "--safety-profile",
        type=Path,
        default=DEFAULT_SAFETY_PROFILE_PATH,
        help="Versioned vehicle twin YAML that supplies non-overridable safety limits.",
    )
    parser.add_argument("--waypoint-timeout", type=float, default=30.0)
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=15.0)
    parser.add_argument("--preflight-wait-seconds", type=float, default=120.0)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Directory for the immutable mission audit artifact.",
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    try:
        from mavsdk import System

        profile = load_safety_profile(arguments.safety_profile)
        gate = SafetyGate(profile.flight_limits())
        mission = authorize_takeoff_waypoint_land(
            gate,
            takeoff_altitude_m=arguments.takeoff_altitude,
            north_m=arguments.north,
            east_m=arguments.east,
            waypoint_altitude_m=arguments.waypoint_altitude,
            hover_duration_s=arguments.hover_seconds,
            waypoint_timeout_s=arguments.waypoint_timeout,
        )
        safety_decision = "approved"
        system = System()
        adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=arguments.preflight_wait_seconds)
        print(f"Connecting to PX4 at {arguments.endpoint}...")
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        print(
            f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, move "
            f"{mission.waypoint.north_m:g} m north and {mission.waypoint.east_m:g} m east, then land."
        )
        execution = await adapter.execute_waypoint_mission(mission)
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
    except Exception as error:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        failure_reason = f"{type(error).__name__}: {error}"
        raise
    finally:
        stop_owned_mavsdk_server(system)
        write_run_artifact(
            getattr(arguments, "artifact_dir", None),
            execution,
            safety_decision,
            outcome,
            failure_reason,
            getattr(adapter, "preflight_telemetry", None),
        )


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
