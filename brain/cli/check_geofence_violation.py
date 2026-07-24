"""Record that an unsafe local waypoint is rejected before PX4 can be armed."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from brain.cli.artifacts import write_run_artifact
from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution
from brain.safety.gate import SafetyGate, SafetyViolation
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, SafetyProfile, load_safety_profile


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prove a geofence-violating waypoint is rejected before a PX4 connection or arm command."
    )
    parser.add_argument(
        "--north", type=float, default=None,
        help="Launch-relative north target in metres. Derived from the profile's own fence when omitted.",
    )
    parser.add_argument("--east", type=float, default=0.0, help="Launch-relative east target in metres.")
    parser.add_argument("--altitude", type=float, default=2.0, help="Target altitude in metres.")
    parser.add_argument("--safety-profile", type=Path, default=DEFAULT_SAFETY_PROFILE_PATH)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    return parser.parse_args(arguments)


def _just_outside_the_fence(profile: SafetyProfile) -> float:
    """A north target the profile's own fence must refuse.

    The probe used to fly at a fixed 45 m, which was outside the fence only
    because the fence happened to be a 30 m box. Widen the contract and the
    probe quietly starts proving the opposite of what it claims: an approved
    waypoint, reported as a passing safety scenario.

    Derived from the fence itself, it cannot drift. A profile with no fence
    falls back to just beyond the radius, which is then the only bound there is.
    """
    fence = profile.allowed_geofence
    if fence is None:
        return profile.max_radius_m * 1.1
    return max(north_m for north_m, _east_m in fence.vertices_m) + 1.0


async def run(arguments: argparse.Namespace) -> None:
    """Treat expected rejection as a completed no-flight safety scenario."""
    execution = MissionExecution.empty()
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    try:
        profile = load_safety_profile(arguments.safety_profile)
        north_m = (
            arguments.north if arguments.north is not None else _just_outside_the_fence(profile)
        )
        command = WaypointCommand(north_m, arguments.east, arguments.altitude)
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
