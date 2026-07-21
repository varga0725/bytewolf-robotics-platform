"""Capture boot and pre-arm telemetry evidence without commanding the vehicle."""

import argparse
import asyncio
from collections.abc import Sequence
from math import isfinite
from pathlib import Path

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import write_run_artifact
from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server
from brain.mission.artifacts import MissionTelemetrySnapshot
from brain.mission.execution import MissionExecution
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def _positive_seconds(value: str) -> float:
    """Parse a bounded timeout used at the pre-arm safety boundary."""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.") from error
    if not isfinite(seconds) or seconds <= 0.0:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.")
    return seconds


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify PX4 boot and pre-arm telemetry without sending a flight command."
    )
    parser.add_argument(
        "--safety-profile",
        type=Path,
        default=DEFAULT_SAFETY_PROFILE_PATH,
        help="Versioned vehicle twin YAML that supplies pre-arm safety limits.",
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
        help="Maximum seconds to wait for PX4 vehicle discovery.",
    )
    parser.add_argument(
        "--preflight-wait-seconds",
        type=_positive_seconds,
        default=120.0,
        help="Maximum seconds to wait for valid navigation and battery telemetry.",
    )
    parser.add_argument(
        "--mavsdk-server-port",
        type=int,
        default=50051,
        help="Local gRPC port for the MAVSDK server owned by this invocation.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Directory for the immutable boot/pre-arm audit artifact.",
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    telemetry: MissionTelemetrySnapshot | None = None
    try:
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error

        profile = load_safety_profile(arguments.safety_profile)
        system = System(port=arguments.mavsdk_server_port)
        adapter = MavsdkMissionAdapter(
            system,
            safety_profile=profile,
            preflight_wait_s=arguments.preflight_wait_seconds,
        )
        print(f"Checking PX4 boot and pre-arm telemetry at {arguments.endpoint}...")
        # Take the endpoint before MAVSDK binds it; the bridge yields to this.
        acquire_px4_link("boot-prearm-check")
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        telemetry = await adapter.verify_preflight()
        safety_decision = "approved"
        outcome = "completed"
        battery_description = (
            f"{telemetry.battery_percent:.1f}%" if telemetry.battery_percent is not None else "unavailable"
        )
        print(
            "Boot/pre-arm approved: navigation, home, global position, and battery telemetry are valid "
            f"(battery {battery_description}). No flight command was sent."
        )
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
            telemetry or getattr(adapter, "preflight_telemetry", None),
        )


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
