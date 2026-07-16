"""Run a bounded takeoff, hover, and PX4 Return-to-Home mission."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import write_run_artifact
from brain.mission.execution import MissionExecution
from brain.mission.flight import authorize_takeoff_return_to_home
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe Return-to-Home mission on PX4 SITL.")
    parser.add_argument("--takeoff-altitude", type=float, default=2.0)
    parser.add_argument("--hover-seconds", type=float, default=3.0)
    parser.add_argument("--landing-timeout", type=float, default=60.0)
    parser.add_argument(
        "--safety-profile",
        type=Path,
        default=DEFAULT_SAFETY_PROFILE_PATH,
        help="Versioned vehicle twin YAML that supplies non-overridable safety limits.",
    )
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
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error

        profile = load_safety_profile(arguments.safety_profile)
        gate = SafetyGate(profile.flight_limits())
        mission = authorize_takeoff_return_to_home(
            gate,
            takeoff_altitude_m=arguments.takeoff_altitude,
            hover_duration_s=arguments.hover_seconds,
            landing_timeout_s=arguments.landing_timeout,
        )
        safety_decision = "approved"
        system = System()
        adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=arguments.preflight_wait_seconds)
        print(f"Connecting to PX4 at {arguments.endpoint}...")
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        print(
            f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, hover for "
            f"{mission.hover_duration_s:g} s, then PX4 returns to launch and lands."
        )
        execution = await adapter.execute_return_to_home_mission(mission)
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
    except Exception as error:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        failure_reason = f"{type(error).__name__}: {error}"
        raise
    finally:
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
