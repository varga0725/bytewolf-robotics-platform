"""Run the visible PX4/Gazebo obstacle-bypass acceptance scenario."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import UTC, datetime
import json
from math import hypot, isfinite
from pathlib import Path
import subprocess
import sys
import time

from brain.adapters.offboard_fallback import FallbackRecord, OffboardFallbackExecutor
from brain.adapters.offboard_route_executor import (
    OffboardRouteExecutionError,
    OffboardRouteExecutor,
    RouteTick,
)
from brain.adapters.offboard_velocity_adapter import MavsdkVelocityAdapter
from brain.navigation.local_replanner import LocalReplanner
from brain.safety.profile import load_safety_profile
from simulation.control.avoidance_route_scenario import (
    AvoidanceGroundTruthSample,
    AvoidanceRouteReport,
    AvoidanceRouteSample,
    evaluate_avoidance_route_run,
)
from simulation.control.mission_route_run import (
    AuditedDrone,
    GazeboObstacleSource,
    MavsdkRouteStateSource,
    _ENDPOINT,
    _LAUNCHER,
    _MODEL_NAME,
    _await_armable,
    _await_connection,
    _confirm_landed,
    _enabled,
    _ensure_termination,
    _last_json_object,
    _simulation_environment,
    _stamp,
    _stream_topic,
)
from simulation.perception.obstacle_scenario import SCENARIO_WORLD, create_service, scan_topic


_POSE_TOPIC = f"/world/{SCENARIO_WORLD}/dynamic_pose/info"
_WALL_CENTER_XY = (0.0, 12.0)
_WALL_HALF_EXTENTS_XY = (1.0, 1.0)
_REQUIRED_CLEARANCE_M = 2.0
_REQUIRED_BYPASS_OFFSET_M = _WALL_HALF_EXTENTS_XY[0] + _REQUIRED_CLEARANCE_M
# Rejoining the final target from the minimum side clearance would cut the
# diagonal through the wall's envelope.  This finite-wall fixture therefore
# holds a larger, geometry-derived detour before it resumes the direct leg.
# Includes a 0.3 m numerical/actuation margin above the analytic 6.2 m rejoin
# geometry, so the scored Gazebo trajectory cannot graze the 2 m envelope.
_DETOUR_RELEASE_OFFSET_M = 6.5


def run_avoidance_route_scenario(
    artifact_directory: Path,
    *,
    altitude_m: float = 2.0,
    target_north_m: float = 15.0,
    route_timeout_s: float = 75.0,
    startup_wait_s: float = 32.0,
    gui: bool = True,
) -> AvoidanceRouteReport:
    """Run a visible, finite-wall avoidance route and persist its evidence."""
    artifact_directory.mkdir(parents=True, exist_ok=True)
    environment = _simulation_environment(gui=gui)
    launcher = subprocess.Popen(
        (str(_LAUNCHER), "lidar-2d"),
        cwd=_LAUNCHER.parents[3],
        stdin=subprocess.DEVNULL,
        env=environment,
    )
    scan_capture = None
    pose_capture = None
    scan_path = artifact_directory / ".avoidance-scans.jsonl"
    pose_path = artifact_directory / ".avoidance-pose.jsonl"
    try:
        time.sleep(startup_wait_s)
        _spawn_bypassable_wall(environment)
        time.sleep(2.0)
        scan_capture = _stream_topic(scan_topic(), scan_path, environment)
        pose_capture = _stream_topic(_POSE_TOPIC, pose_path, environment)
        time.sleep(2.0)
        report = asyncio.run(
            _fly_avoidance_route(
                altitude_m=altitude_m,
                target_north_m=target_north_m,
                route_timeout_s=route_timeout_s,
                scan_path=scan_path,
                pose_path=pose_path,
            )
        )
    finally:
        for process in (scan_capture, pose_capture, launcher):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        scan_path.unlink(missing_ok=True)
        pose_path.unlink(missing_ok=True)

    destination = artifact_directory / f"avoidance-route-{_stamp()}.json"
    destination.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    print(f"evidence: {destination}")
    return report


async def _fly_avoidance_route(
    *,
    altitude_m: float,
    target_north_m: float,
    route_timeout_s: float,
    scan_path: Path,
    pose_path: Path,
) -> AvoidanceRouteReport:
    from mavsdk import System

    profile = load_safety_profile()
    assert profile.offboard is not None and profile.shield is not None
    scenario_profile = replace(
        profile,
        offboard=_enabled(profile.offboard),
        shield=_enabled(profile.shield),
    )
    system = System()
    await system.connect(system_address=_ENDPOINT)
    await _await_connection(system)
    drone = AuditedDrone(system)
    adapter = MavsdkVelocityAdapter(drone)
    fallback_executor = OffboardFallbackExecutor(drone, adapter)
    samples: list[AvoidanceRouteSample] = []
    ground_truth_samples: list[AvoidanceGroundTruthSample] = []
    started = time.monotonic()
    armed_at_s = 0.0
    ended_at_s = 0.0
    control_started: float | None = None
    control_ended: float | None = None
    fallback: FallbackRecord | None = None
    route_reached = False
    landed = False
    arm_attempted = False
    execution_error: str | None = None
    ground_truth_stop = asyncio.Event()
    ground_truth_task: asyncio.Task[None] | None = None

    async def monitor_ground_truth() -> None:
        while not ground_truth_stop.is_set():
            pose = _fresh_pose(pose_path)
            if pose is not None:
                x_m, y_m, age_s = pose
                ground_truth_samples.append(
                    AvoidanceGroundTruthSample(
                        at_s=round(time.monotonic() - started, 3),
                        x_m=x_m,
                        y_m=y_m,
                        clearance_m=_wall_clearance(x_m, y_m),
                        age_s=age_s,
                    )
                )
            try:
                await asyncio.wait_for(ground_truth_stop.wait(), timeout=0.2)
            except TimeoutError:
                pass

    def record_tick(tick: RouteTick) -> None:
        samples.append(
            AvoidanceRouteSample(
                at_s=round(time.monotonic() - started, 3),
                primary_verdict=tick.primary_verdict,
                selected_mode=tick.selected_mode,
                commanded_x_m_s=tick.commanded_velocity.x_m_s,
                commanded_y_m_s=tick.commanded_velocity.y_m_s,
                commanded_z_m_s=tick.commanded_velocity.z_m_s,
                delivered=tick.delivered,
                exact_translational_stop=(
                    tick.commanded_velocity.x_m_s
                    == tick.commanded_velocity.y_m_s
                    == tick.commanded_velocity.z_m_s
                    == 0.0
                ),
            )
        )

    try:
        await _await_armable(drone)
        await drone.action.set_takeoff_altitude(altitude_m)
        ground_truth_task = asyncio.create_task(monitor_ground_truth())
        armed_at_s = time.monotonic() - started
        arm_attempted = True
        await drone.action.arm()
        await drone.action.takeoff()
        await asyncio.sleep(9.0)
        state_source = await MavsdkRouteStateSource.create(
            drone, target_north_m=target_north_m
        )
        executor = OffboardRouteExecutor(
            profile=scenario_profile,
            adapter=adapter,
            fallback_executor=fallback_executor,
            state_source=state_source,
            obstacle_source=GazeboObstacleSource(scan_path, profile.vehicle_id),
            now=lambda: datetime.now(UTC),
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
            stream_hz=5.0,
            telemetry_max_age_s=0.5,
            max_vertical_speed_m_s=0.5,
            slowdown_radius_m=3.0,
            replanner=LocalReplanner(
                lateral_speed_m_s=0.4,
                # 6.2 m side-step plus an along-path advance to the target's
                # longitudinal station is deliberately bounded, not open-ended.
                max_detour_s=60.0,
                required_bypass_offset_m=_DETOUR_RELEASE_OFFSET_M,
            ),
            tick_observer=record_tick,
        )
        control_started = time.monotonic()
        try:
            # A 1 m arrival radius can still intersect the finite wall's 2 m
            # safety envelope near its far corner.  This acceptance route must
            # actually clear the obstacle, not merely arrive close to its goal.
            await executor.run(arrival_tolerance_m=0.2, timeout_s=route_timeout_s)
            route_reached = True
        finally:
            control_ended = time.monotonic()
    except OffboardRouteExecutionError as error:
        fallback = error.fallback
        execution_error = f"{error.reason.value}: {error}"
    except Exception as error:
        execution_error = f"{type(error).__name__}: {error}"
    finally:
        if arm_attempted:
            if route_reached:
                fallback, landed, execution_error = await _normal_land_or_escalate(
                    drone=drone,
                    fallback_executor=fallback_executor,
                    sequence=scenario_profile.offboard.fallback_sequence,
                )
            else:
                fallback, landed, execution_error = await _ensure_termination(
                    drone=drone,
                    fallback_executor=fallback_executor,
                    sequence=scenario_profile.offboard.fallback_sequence,
                    fallback=fallback,
                    execution_error=execution_error,
                    rpc_timeout_s=5.0,
                    confirm_timeout_s=30.0,
                    retry_confirm_timeout_s=15.0,
                )
            ended_at_s = time.monotonic() - started
            ground_truth_stop.set()
            if ground_truth_task is not None:
                await ground_truth_task

    return evaluate_avoidance_route_run(
        samples=samples,
        ground_truth_samples=ground_truth_samples,
        flown_seconds=max((control_ended or time.monotonic()) - (control_started or started), 0.001),
        armed_at_s=armed_at_s,
        ended_at_s=ended_at_s,
        required_clearance_m=_REQUIRED_CLEARANCE_M,
        required_bypass_offset_m=_REQUIRED_BYPASS_OFFSET_M,
        goto_location_calls=drone.action.goto_location_calls,
        route_reached=route_reached,
        fallback_used=fallback is not None,
        landed=landed,
        execution_error=execution_error,
    )


async def _normal_land_or_escalate(*, drone, fallback_executor, sequence):
    """Try normal land, then use the bounded emergency termination path."""
    try:
        await asyncio.wait_for(drone.action.land(), timeout=5.0)
        if await _confirm_landed(drone, timeout_s=15.0):
            return None, True, None
    except Exception as error:
        normal_error = f"normal landing failed: {error}"
    else:
        normal_error = "normal landing was not confirmed"
    fallback, landed, error = await _ensure_termination(
        drone=drone,
        fallback_executor=fallback_executor,
        sequence=sequence,
        fallback=None,
        execution_error=normal_error,
        rpc_timeout_s=5.0,
        confirm_timeout_s=30.0,
        retry_confirm_timeout_s=15.0,
    )
    return fallback, landed, error


def _fresh_pose(pose_path: Path, *, max_age_s: float = 0.5) -> tuple[float, float, float] | None:
    try:
        age_s = (datetime.now(UTC) - datetime.fromtimestamp(pose_path.stat().st_mtime, tz=UTC)).total_seconds()
    except OSError:
        return None
    if age_s < 0.0 or age_s > max_age_s:
        return None
    message = _last_json_object(pose_path)
    if not isinstance(message, dict):
        return None
    for pose in message.get("pose", []):
        if isinstance(pose, dict) and pose.get("name") == _MODEL_NAME:
            position = pose.get("position", {})
            try:
                x_m = float(position["x"])
                y_m = float(position["y"])
                if not isfinite(x_m) or not isfinite(y_m):
                    return None
                return x_m, y_m, age_s
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _wall_clearance(x: float, y: float) -> float:
    dx = max(abs(x - _WALL_CENTER_XY[0]) - _WALL_HALF_EXTENTS_XY[0], 0.0)
    dy = max(abs(y - _WALL_CENTER_XY[1]) - _WALL_HALF_EXTENTS_XY[1], 0.0)
    return hypot(dx, dy)


def _spawn_bypassable_wall(environment: dict[str, str]) -> None:
    sdf = (
        '<sdf version="1.9"><model name="avoidance_route_wall"><static>true</static>'
        f'<pose>{_WALL_CENTER_XY[0]} {_WALL_CENTER_XY[1]} 1.5 0 0 0</pose><link name="l">'
        '<collision name="c"><geometry><box><size>2 2 3</size></box></geometry></collision>'
        '<visual name="v"><geometry><box><size>2 2 3</size></box></geometry></visual>'
        '</link></model></sdf>'
    )
    result = subprocess.run(
        (
            "gz", "service", "-s", create_service(),
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000", "--req",
            'name: "avoidance_route_wall", allow_renaming: false, '
            f"sdf: {json.dumps(sdf)}",
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )
    if "true" not in result.stdout.lower():
        raise RuntimeError(
            f"Could not spawn the avoidance wall: {result.stdout.strip() or result.stderr.strip()}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run visible PX4/Gazebo obstacle avoidance evidence.")
    parser.add_argument("--artifact-dir", type=Path, default=Path("simulation/artifacts/control/avoidance-route"))
    parser.add_argument("--route-timeout-seconds", type=float, default=75.0)
    parser.add_argument("--startup-wait-seconds", type=float, default=32.0)
    parser.add_argument("--no-gui", action="store_true")
    args = parser.parse_args()
    report = run_avoidance_route_scenario(
        args.artifact_dir,
        route_timeout_s=args.route_timeout_seconds,
        startup_wait_s=args.startup_wait_seconds,
        gui=not args.no_gui,
    )
    for finding in report.findings:
        print(f"FAIL: {finding}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
