"""Record that an unsafe local waypoint is rejected before PX4 can be armed."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from brain.cli.artifacts import write_run_artifact
from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution
from brain.safety.gate import SafetyGate, SafetyViolation
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prove a geofence-violating waypoint is rejected before a PX4 connection or arm command."
    )
    parser.add_argument("--north", type=float, default=45.0, help="Launch-relative north target in metres.")
    parser.add_argument("--east", type=float, default=0.0, help="Launch-relative east target in metres.")
    parser.add_argument("--altitude", type=float, default=2.0, help="Target altitude in metres.")
    parser.add_argument("--safety-profile", type=Path, default=DEFAULT_SAFETY_PROFILE_PATH)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    """Treat expected rejection as a completed no-flight safety scenario."""
    execution = MissionExecution.empty()
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    try:
        profile = load_safety_profile(arguments.safety_profile)
        command = WaypointCommand(arguments.north, arguments.east, arguments.altitude)
        try:
            SafetyGate(profile.flight_limits()).evaluate(command)
        except SafetyViolation as error:
            safety_decision = "rejected"
            outcome = "completed"
            failure_reason = f"{type(error).__name__}: {error}"
            print(f"Geofence violation rejected before arm: {error}")
            return
        safety_decision = "approved"
        failure_reason = "SafetyViolation: target is inside the allowed geofence; violation probe is invalid."
        raise SafetyViolation(failure_reason)
    except Exception:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        raise
    finally:
        write_run_artifact(
            getattr(arguments, "artifact_dir", None),
            execution,
            safety_decision,
            outcome,
            failure_reason,
        )


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
