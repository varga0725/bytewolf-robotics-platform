"""Exercise bounded HOLD and LAND interruption paths against PX4 SITL."""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import mandatory_telemetry_history_path, recorded_execution, write_run_artifact
from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from brain.mission.execution import MissionExecution
from brain.mission.flight import InterruptionAction, authorize_takeoff_interrupt_land
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay
from brain.telemetry.persistence import TelemetryHistoryStore


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Take off, issue one controlled HOLD or LAND interruption, then confirm landing."
    )
    parser.add_argument("--altitude", type=float, default=2.0)
    parser.add_argument("--interrupt-after-seconds", type=float, default=3.0)
    parser.add_argument("--interruption-action", choices=tuple(InterruptionAction), required=True)
    parser.add_argument(
        "--hold-cleanup-seconds",
        type=float,
        default=1.0,
        help="Bounded HOLD observation before the mandatory cleanup landing.",
    )
    parser.add_argument("--safety-profile", type=Path, default=DEFAULT_SAFETY_PROFILE_PATH)
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=15.0)
    parser.add_argument("--preflight-wait-seconds", type=float, default=120.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument(
        "--dashboard-snapshot",
        type=Path,
        default=Path("simulation/artifacts/dashboard/live-telemetry.json"),
    )
    parser.add_argument("--telemetry-history", type=Path, default=None)
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    relay_stop_event: asyncio.Event | None = None
    relay_task: asyncio.Task[None] | None = None
    try:
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:
            raise RuntimeError("MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt") from error
        profile = load_safety_profile(arguments.safety_profile)
        mission = authorize_takeoff_interrupt_land(
            SafetyGate(profile.flight_limits()),
            arguments.altitude,
            arguments.interrupt_after_seconds,
            InterruptionAction(arguments.interruption_action),
            arguments.hold_cleanup_seconds,
        )
        safety_decision = "approved"
        system = System(port=arguments.mavsdk_server_port)
        adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=arguments.preflight_wait_seconds)
        print(f"Connecting to PX4 at {arguments.endpoint}...")
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        relay_stop_event = asyncio.Event()
        history_store = TelemetryHistoryStore(
            mandatory_telemetry_history_path(arguments.artifact_dir, arguments.telemetry_history)
        )
        relay_task = asyncio.create_task(
            MavsdkTelemetryRelay(
                system,
                arguments.dashboard_snapshot,
                on_event=history_store.append,
            ).run(relay_stop_event)
        )
        print(
            f"Approved: take off to {mission.takeoff.target_altitude_m:g} m, then "
            f"{mission.interruption_action.value.upper()} after {mission.interrupt_after_s:g} s."
        )
        execution = await adapter.execute_controlled_interruption_mission(mission)
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
    except Exception as error:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        failure_reason = f"{type(error).__name__}: {error}"
        execution = recorded_execution(adapter, execution)
        raise
    finally:
        if relay_stop_event is not None:
            relay_stop_event.set()
        if relay_task is not None:
            try:
                await relay_task
            except Exception as error:
                print(f"Dashboard telemetry relay stopped: {type(error).__name__}: {error}")
        stop_owned_mavsdk_server(system)
        write_run_artifact(
            arguments.artifact_dir,
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
