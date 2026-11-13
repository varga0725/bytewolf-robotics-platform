"""Run the Monday demo: takeoff, one waypoint, PX4 RTL, land, disarm."""

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import prepare_flight_run_recording, recorded_execution, write_run_artifact
from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server
from brain.mission.demo_point_rtl import (
    MISSION_TYPE,
    authorize_demo_point_rtl,
    demo_point_rtl_evidence_document,
    load_demo_plan_config,
    load_demo_point_rtl_config,
    validate_demo_point_rtl_progress,
)
from brain.mission.demo_position_sampler import sample_positions_while
from brain.mission.demo_reviewed_plan import require_matching_demo_approval
from brain.mission.simulation_target import require_simulation_target
from brain.navigation.waypoints import GlobalPosition
from brain.mission.execution import MissionExecution
from brain.safety.deployment import actuation_request_sha256, add_deployment_arguments, file_sha256
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay
from brain.telemetry.persistence import TelemetryHistoryStore
from shared.safety.execution_gate import require_mission_execution_enabled


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the demo-point-rtl SITL mission through PX4 Action modes."
    )
    parser.add_argument(
        "--safety-profile",
        type=Path,
        default=DEFAULT_SAFETY_PROFILE_PATH,
        help="Versioned vehicle twin YAML that supplies non-overridable safety limits.",
    )
    parser.add_argument("--endpoint", default="udpin://127.0.0.1:14540")
    parser.add_argument("--connection-timeout", type=float, default=30.0)
    parser.add_argument("--preflight-wait-seconds", type=float, default=120.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument(
        "--dashboard-snapshot",
        type=Path,
        default=Path("simulation/artifacts/dashboard/live-telemetry.json"),
    )
    parser.add_argument("--telemetry-history", type=Path, default=None)
    parser.add_argument("--px4-ulog", type=Path, default=None)
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=None,
        help="Approved demo-point-rtl plan written by the Control Room review step.",
    )
    add_deployment_arguments(parser, actuation_capable=True)
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    require_mission_execution_enabled()
    if arguments.plan_file is not None:
        require_matching_demo_approval(arguments.plan_file)
        config = load_demo_plan_config(arguments.plan_file)
    else:
        config = load_demo_point_rtl_config()
    recording = prepare_flight_run_recording(arguments.artifact_dir, arguments.telemetry_history)
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    dashboard_stop_event: asyncio.Event | None = None
    dashboard_relay_task: asyncio.Task[None] | None = None
    try:
        profile = load_safety_profile(arguments.safety_profile)
        gate = SafetyGate(profile.flight_limits())
        mission = authorize_demo_point_rtl(gate, config)
        safety_decision = "approved"
        request_sha256 = actuation_request_sha256(
            {
                "operation": MISSION_TYPE,
                "vehicle_id": profile.vehicle_id,
                "north_m": config.north_m,
                "east_m": config.east_m,
                "takeoff_altitude_m": config.takeoff_altitude_m,
            }
        )
        require_simulation_target(
            deployment_mode=arguments.deployment_mode,
            endpoint=arguments.endpoint,
            profile=profile,
            operation=MISSION_TYPE,
            request_sha256=request_sha256,
            safety_profile_sha256=file_sha256(arguments.safety_profile),
            permit_path=arguments.physical_actuation_permit,
        )
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error
        system = System(port=arguments.mavsdk_server_port)
        adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=arguments.preflight_wait_seconds)
        print(f"Connecting to PX4 at {arguments.endpoint}...")
        acquire_px4_link(MISSION_TYPE)
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        dashboard_stop_event = asyncio.Event()
        history_store = TelemetryHistoryStore(recording.telemetry_history_path, recording.run_id)
        dashboard_relay_task = asyncio.create_task(
            MavsdkTelemetryRelay(
                system,
                arguments.dashboard_snapshot,
                on_event=history_store.append,
            ).run(dashboard_stop_event)
        )
        print(
            f"Approved demo-point-rtl: take off to {config.takeoff_altitude_m:g} m, "
            f"goto ({config.north_m:g} m north, {config.east_m:g} m east), RTL, land, disarm."
        )
        home = await anext(system.telemetry.home())
        home_position = GlobalPosition(
            float(home.latitude_deg), float(home.longitude_deg), float(home.absolute_altitude_m)
        )
        position_samples: list[dict[str, object]] = []
        sampler_stop = asyncio.Event()
        sampler_task = asyncio.create_task(
            sample_positions_while(
                system, home_position, stop=sampler_stop, sink=position_samples
            )
        )
        try:
            execution = await adapter.execute_demo_point_rtl_mission(mission)
        finally:
            sampler_stop.set()
            await sampler_task
        validate_demo_point_rtl_progress(execution, hover_duration_s=config.hover_duration_s)
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
        _maybe_write_acceptance_evidence(
            execution=execution,
            config=config,
            outcome=outcome,
            endpoint=arguments.endpoint,
            positions=position_samples,
            sitl_verification=getattr(adapter, "_demo_sitl_proof", {}),
        )
    except Exception as error:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        failure_reason = f"{type(error).__name__}: {error}"
        execution = recorded_execution(adapter, execution)
        raise
    finally:
        relay_error: Exception | None = None
        if dashboard_stop_event is not None:
            dashboard_stop_event.set()
        if dashboard_relay_task is not None:
            try:
                await dashboard_relay_task
            except Exception as error:
                relay_error = error
                if failure_reason is None:
                    outcome = "failed"
                    failure_reason = f"{type(error).__name__}: mandatory telemetry relay failed: {error}"
        stop_owned_mavsdk_server(system)
        write_run_artifact(
            getattr(arguments, "artifact_dir", None),
            execution,
            safety_decision,
            outcome,
            failure_reason,
            getattr(adapter, "preflight_telemetry", None),
            recording.run_id,
            arguments.px4_ulog,
        )
        if relay_error is not None and failure_reason and "mandatory telemetry relay failed" in failure_reason:
            raise relay_error


def _maybe_write_acceptance_evidence(
    *,
    execution,
    config,
    outcome: str,
    endpoint: str,
    positions: list[dict[str, object]],
    sitl_verification: dict[str, object],
) -> None:
    if os.environ.get("BYTEWOLF_SITL_ACCEPTANCE") != "1":
        return
    from simulation.baseline import load_baseline

    baseline = load_baseline()
    document = demo_point_rtl_evidence_document(
        execution=execution,
        config=config,
        outcome=outcome,
        endpoint=endpoint,
        px4_version=baseline.px4_describe,
        baseline_profile=baseline.profile,
        baseline_px4_commit=baseline.px4_commit,
        baseline_gazebo_version=baseline.gazebo_sim_version,
        simulator_name=os.environ.get("BYTEWOLF_SIMULATOR", "gz_x500"),
        sitl_verification=sitl_verification,
        positions=positions,
        started_at=execution.events[0].timestamp if execution.events else datetime.now(UTC),
    )
    directory = Path("simulation/artifacts/demo-point-rtl")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(document, indent=2)
    (directory / f"demo-point-rtl-{stamp}.json").write_text(payload, encoding="utf-8")
    (directory / "latest.json").write_text(payload, encoding="utf-8")


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
