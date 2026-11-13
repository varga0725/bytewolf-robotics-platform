"""Run the Monday demo waypoint loop via the MAVSDK Mission plugin."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from brain.adapters.demo_waypoint_mission_adapter import execute_demo_waypoint_mission
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import prepare_flight_run_recording, recorded_execution, write_run_artifact
from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server
from brain.mission.demo_position_sampler import sample_positions_while
from brain.mission.demo_trajectory_plot import render_demo_trajectory_png
from brain.mission.demo_visual_capture import DemoFlightScreenCapture, resolve_screen_capture_mode
from brain.mission.demo_waypoint_mission import (
    MISSION_TYPE,
    authorize_demo_waypoint_mission,
    demo_waypoint_mission_evidence_document,
    load_demo_waypoint_mission_config,
    load_demo_waypoint_plan_config,
    mission_items_document,
    validate_demo_waypoint_mission_progress,
)
from brain.mission.demo_waypoint_reviewed_plan import require_matching_demo_waypoint_approval
from brain.mission.execution import MissionExecution
from brain.mission.simulation_target import require_simulation_target
from brain.navigation.waypoints import GlobalPosition
from brain.safety.deployment import actuation_request_sha256, add_deployment_arguments, file_sha256
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay
from brain.telemetry.ulog import wait_for_px4_ulog_archive
from brain.telemetry.persistence import TelemetryHistoryStore
from shared.safety.execution_gate import require_mission_execution_enabled


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the demo-waypoint-mission SITL route through the MAVSDK Mission plugin."
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
        help="Approved demo-waypoint-mission plan written by the Control Room review step.",
    )
    parser.add_argument(
        "--skip-optional-screen-capture",
        action="store_true",
        help="Do not attempt optional gz GUI screen capture.",
    )
    parser.add_argument(
        "--screen-capture-mode",
        choices=("x11", "xvfb"),
        default=None,
        help="Optional capture backend (overrides BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE).",
    )
    add_deployment_arguments(parser, actuation_capable=True)
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    require_mission_execution_enabled()
    sitl_acceptance = os.environ.get("BYTEWOLF_SITL_ACCEPTANCE") == "1"
    if sitl_acceptance and arguments.plan_file is None:
        raise RuntimeError(
            "SITL acceptance requires --plan-file with a matching reviewed approval record."
        )
    profile = load_safety_profile(arguments.safety_profile)
    if arguments.plan_file is not None:
        require_matching_demo_waypoint_approval(arguments.plan_file)
        config = load_demo_waypoint_plan_config(arguments.plan_file)
    else:
        config = load_demo_waypoint_mission_config(profile=profile)
    recording = prepare_flight_run_recording(arguments.artifact_dir, arguments.telemetry_history)
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    dashboard_stop_event: asyncio.Event | None = None
    dashboard_relay_task: asyncio.Task[None] | None = None
    flight_capture: DemoFlightScreenCapture | None = None
    screen_capture_result: dict[str, object] | None = None
    position_samples: list[dict[str, object]] = []
    mission_started_monotonic: float | None = None
    ulog_failure_reason: str | None = None
    try:
        gate = SafetyGate(profile.flight_limits())
        authorize_demo_waypoint_mission(gate, config, profile=profile)
        safety_decision = "approved"
        request_sha256 = actuation_request_sha256(
            {
                "operation": MISSION_TYPE,
                "vehicle_id": profile.vehicle_id,
                "waypoint_count": len(config.waypoints),
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
            f"Approved demo-waypoint-mission: takeoff {config.takeoff_altitude_m:g} m, "
            f"{len(config.waypoints)} waypoints, RTL, land, disarm."
        )
        home = await anext(system.telemetry.home())
        home_position = GlobalPosition(
            float(home.latitude_deg), float(home.longitude_deg), float(home.absolute_altitude_m)
        )
        sampler_stop = asyncio.Event()
        sampler_task = asyncio.create_task(
            sample_positions_while(system, home_position, stop=sampler_stop, sink=position_samples)
        )
        evidence_directory = Path("simulation/artifacts/demo-waypoint-mission")
        evidence_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if sitl_acceptance and not arguments.skip_optional_screen_capture:
            capture_mode = resolve_screen_capture_mode(arguments.screen_capture_mode)
            if capture_mode is not None:
                max_capture_s = float(
                    os.environ.get(
                        "BYTEWOLF_DEMO_SCREEN_CAPTURE_MAX_S",
                        str(config.mission_timeout_s + config.rtl_timeout_s + 120.0),
                    )
                )
                flight_capture = DemoFlightScreenCapture(
                    evidence_directory / f"screen-{evidence_stamp}.mp4",
                    mode=capture_mode,
                    max_duration_s=max_capture_s,
                    evidence_directory=evidence_directory,
                )
                screen_capture_result = (await asyncio.to_thread(flight_capture.start)).as_dict()
        try:
            mission_started_monotonic = time.monotonic()
            execution = await execute_demo_waypoint_mission(adapter, config)
        finally:
            sampler_stop.set()
            await sampler_task
            if flight_capture is not None:
                screen_capture_result = (await asyncio.to_thread(flight_capture.stop)).as_dict()
        validate_demo_waypoint_mission_progress(
            execution,
            items_reached=tuple(getattr(adapter, "_demo_mission_items_reached", ())),
        )
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
        ulog_relative: str | None = None
        ulog_status = "missing"
        if sitl_acceptance and arguments.artifact_dir is not None:
            ulog_relative, ulog_status, ulog_failure_reason = await wait_for_px4_ulog_archive(
                arguments.artifact_dir,
                recording.run_id,
                px4_ulog=arguments.px4_ulog,
                run_started_monotonic=mission_started_monotonic,
            )
        evidence_paths = _write_acceptance_evidence(
            execution=execution,
            config=config,
            outcome=outcome,
            endpoint=arguments.endpoint,
            positions=position_samples,
            sitl_verification=getattr(adapter, "_demo_sitl_proof", {}),
            adapter=adapter,
            screen_capture_status=screen_capture_result,
            artifact_dir=arguments.artifact_dir,
            run_id=recording.run_id,
            evidence_stamp=evidence_stamp,
            ulog_relative=ulog_relative,
            ulog_status=ulog_status,
            ulog_failure_reason=ulog_failure_reason,
        )
        if evidence_paths:
            print(f"Evidence written under {evidence_paths['directory']}")
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
        peak = getattr(adapter, "_peak_relative_altitude_m", None) if adapter is not None else None
        artifact_path = write_run_artifact(
            getattr(arguments, "artifact_dir", None),
            execution,
            safety_decision,
            outcome,
            failure_reason,
            getattr(adapter, "preflight_telemetry", None) if adapter is not None else None,
            recording.run_id,
            arguments.px4_ulog,
            peak,
        )
        if sitl_acceptance and safety_decision == "approved" and outcome != "completed":
            if arguments.artifact_dir is not None and mission_started_monotonic is not None:
                (
                    _ulog_rel,
                    _ulog_status,
                    ulog_failure_reason,
                ) = await wait_for_px4_ulog_archive(
                    arguments.artifact_dir,
                    recording.run_id,
                    px4_ulog=arguments.px4_ulog,
                    run_started_monotonic=mission_started_monotonic,
                )
            _write_failed_acceptance_record(
                execution=recorded_execution(adapter, execution),
                config=config,
                endpoint=arguments.endpoint,
                failure_reason=failure_reason,
                artifact_path=artifact_path,
                artifact_dir=arguments.artifact_dir,
                run_id=recording.run_id,
                positions=position_samples,
                adapter=adapter,
                screen_capture_status=screen_capture_result,
            )
        if relay_error is not None and failure_reason and "mandatory telemetry relay failed" in failure_reason:
            raise relay_error


def _write_acceptance_evidence(
    *,
    execution,
    config,
    outcome: str,
    endpoint: str,
    positions: list[dict[str, object]],
    sitl_verification: dict[str, object],
    adapter: MavsdkMissionAdapter,
    screen_capture_status: dict[str, object] | None,
    artifact_dir: Path | None,
    run_id: str,
    evidence_stamp: str,
    ulog_relative: str | None = None,
    ulog_status: str = "missing",
    ulog_failure_reason: str | None = None,
) -> dict[str, str] | None:
    if os.environ.get("BYTEWOLF_SITL_ACCEPTANCE") != "1":
        return None
    from simulation.baseline import load_baseline

    baseline = load_baseline()
    directory = Path("simulation/artifacts/demo-waypoint-mission")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = evidence_stamp
    items = mission_items_document(config)
    trajectory_path = directory / f"trajectory-{stamp}.png"
    render_demo_trajectory_png(
        positions=positions,
        mission_items=items,
        output_path=trajectory_path,
    )
    screen_path = None
    if screen_capture_status and screen_capture_status.get("status") == "captured":
        screen_path = str(screen_capture_status.get("relative_path"))

    if artifact_dir is not None and ulog_relative is None:
        ulog_relative, ulog_status = _resolve_ulog_evidence(artifact_dir, run_id)

    document = demo_waypoint_mission_evidence_document(
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
        mission_items=items,
        items_reached=list(getattr(adapter, "_demo_mission_items_reached", [])),
        peak_relative_altitude_m=getattr(adapter, "_peak_relative_altitude_m", None),
        disarm_reason=getattr(adapter, "_demo_disarm_reason", None),
        px4_ulog_relative_path=ulog_relative,
        trajectory_png_relative_path=str(trajectory_path.relative_to(directory)),
        optional_screen_capture_relative_path=screen_path,
        screen_capture_status=screen_capture_status,
        started_at=execution.events[0].timestamp if execution.events else datetime.now(UTC),
    )
    document["ulog_status"] = ulog_status
    if ulog_status != "captured":
        document["overall_status"] = "failed"
        document["failure_reason"] = ulog_failure_reason or "PX4 ULog was not archived before evidence was written."
    payload = json.dumps(document, indent=2)
    run_name = f"demo-waypoint-mission-{stamp}.json"
    (directory / run_name).write_text(payload, encoding="utf-8")
    (directory / "latest.json").write_text(payload, encoding="utf-8")
    return {"directory": str(directory), "run": run_name}


def _resolve_ulog_evidence(artifact_dir: Path | None, run_id: str) -> tuple[str | None, str]:
    if artifact_dir is None:
        return None, "missing"
    manifest = artifact_dir / "px4-ulogs" / f"{run_id}.manifest.json"
    if not manifest.is_file():
        return None, "missing"
    manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
    if manifest_document.get("status") == "captured":
        return str(manifest_document.get("relative_path")), "captured"
    return None, "missing"


def _write_failed_acceptance_record(
    *,
    execution,
    config,
    endpoint: str,
    failure_reason: str | None,
    artifact_path: Path | None,
    artifact_dir: Path | None,
    run_id: str,
    positions: list[dict[str, object]],
    adapter: MavsdkMissionAdapter | None,
    screen_capture_status: dict[str, object] | None,
) -> None:
    from simulation.baseline import load_baseline

    directory = Path("simulation/artifacts/demo-waypoint-mission")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    items = mission_items_document(config)
    trajectory_path = directory / f"trajectory-{stamp}.png"
    if positions:
        render_demo_trajectory_png(
            positions=positions,
            mission_items=items,
            output_path=trajectory_path,
        )
    ulog_relative, ulog_status = _resolve_ulog_evidence(artifact_dir, run_id)
    baseline = load_baseline()
    document = demo_waypoint_mission_evidence_document(
        execution=execution,
        config=config,
        outcome="failed",
        endpoint=endpoint,
        px4_version=baseline.px4_describe,
        baseline_profile=baseline.profile,
        baseline_px4_commit=baseline.px4_commit,
        baseline_gazebo_version=baseline.gazebo_sim_version,
        simulator_name=os.environ.get("BYTEWOLF_SIMULATOR", "gz_x500"),
        sitl_verification=getattr(adapter, "_demo_sitl_proof", {}) if adapter is not None else {},
        positions=positions,
        mission_items=items,
        items_reached=list(getattr(adapter, "_demo_mission_items_reached", [])) if adapter else [],
        peak_relative_altitude_m=getattr(adapter, "_peak_relative_altitude_m", None) if adapter else None,
        disarm_reason=getattr(adapter, "_demo_disarm_reason", None) if adapter else None,
        px4_ulog_relative_path=ulog_relative,
        trajectory_png_relative_path=str(trajectory_path.relative_to(directory)) if positions else None,
        optional_screen_capture_relative_path=(
            str(screen_capture_status.get("relative_path"))
            if screen_capture_status and screen_capture_status.get("status") == "captured"
            else None
        ),
        screen_capture_status=screen_capture_status,
        started_at=execution.events[0].timestamp if execution.events else datetime.now(UTC),
    )
    document["overall_status"] = "failed"
    document["failure_reason"] = failure_reason
    document["ulog_status"] = ulog_status
    document["mission_artifact_path"] = str(artifact_path) if artifact_path is not None else None
    payload = json.dumps(document, indent=2)
    (directory / f"demo-waypoint-mission-{stamp}.json").write_text(payload, encoding="utf-8")
    (directory / "latest.json").write_text(payload, encoding="utf-8")


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
