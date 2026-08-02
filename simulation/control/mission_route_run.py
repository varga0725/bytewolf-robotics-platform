"""Prove the shielded mission route against real PX4 SITL, Gazebo and lidar."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import json
from math import cos, hypot, isfinite, radians
import os
from pathlib import Path
import subprocess
import sys
import time

from brain.adapters.offboard_fallback import FallbackRecord, OffboardFallbackExecutor
from brain.adapters.offboard_route_executor import (
    OffboardRouteExecutionError,
    OffboardRouteExecutor,
    RouteState,
    RouteTerminalReason,
    RouteTick,
)
from brain.adapters.offboard_velocity_adapter import MavsdkVelocityAdapter
from brain.safety.profile import load_safety_profile
from simulation.control.mission_route_scenario import (
    MissionGroundTruthSample,
    MissionRouteSample,
    MissionRouteReport,
    evaluate_mission_route_run,
)
from simulation.control.shield_run import (
    _MODEL_NAME,
    _await_armable,
    _await_connection,
    _enabled,
    _last_json_object,
    _latest_observation,
    _stamp,
    _stream_topic,
)
from simulation.perception.obstacle_scenario import (
    SCENARIO_WORLD,
    create_service,
    scan_topic,
)


_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _ROOT / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh"
_ENDPOINT = "udpin://0.0.0.0:14540"
_POSE_TOPIC = f"/world/{SCENARIO_WORLD}/dynamic_pose/info"
_EARTH_RADIUS_M = 6_371_000.0
_ROUTE_OBSTACLE_XY = (0.0, 12.0)
_ROUTE_OBSTACLE_HALF_EXTENTS_XY = (12.0, 1.0)


class MavsdkRouteStateSource:
    """Read one coherent-enough position/heading pair for a fixed local target."""

    def __init__(
        self,
        drone,
        *,
        origin,
        target_north_m: float,
        target_east_m: float,
        target_relative_altitude_m: float,
    ) -> None:
        self._drone = drone
        self._origin = origin
        self._target_north_m = target_north_m
        self._target_east_m = target_east_m
        self._target_relative_altitude_m = target_relative_altitude_m

    @classmethod
    async def create(
        cls, drone, *, target_north_m: float, target_east_m: float = 0.0
    ) -> "MavsdkRouteStateSource":
        origin = await anext(drone.telemetry.position())
        return cls(
            drone,
            origin=origin,
            target_north_m=target_north_m,
            target_east_m=target_east_m,
            target_relative_altitude_m=float(origin.relative_altitude_m),
        )

    async def sample(self) -> RouteState:
        position, attitude = await asyncio.gather(
            anext(self._drone.telemetry.position()),
            anext(self._drone.telemetry.attitude_euler()),
        )
        north_m = (
            radians(position.latitude_deg - self._origin.latitude_deg)
            * _EARTH_RADIUS_M
        )
        east_m = (
            radians(position.longitude_deg - self._origin.longitude_deg)
            * _EARTH_RADIUS_M
            * cos(radians(self._origin.latitude_deg))
        )
        heading = _canonical_heading(float(attitude.yaw_deg))
        return RouteState(
            north_error_m=self._target_north_m - north_m,
            east_error_m=self._target_east_m - east_m,
            altitude_error_m=(
                self._target_relative_altitude_m
                - float(position.relative_altitude_m)
            ),
            heading_deg=heading,
            # MAVSDK exposes no producer timestamp on these samples. This is
            # honestly the paired receipt time, bounded by the executor TTL.
            observed_at=datetime.now(UTC),
        )


class GazeboObstacleSource:
    def __init__(self, scan_path: Path, vehicle_id: str) -> None:
        self._scan_path = scan_path
        self._vehicle_id = vehicle_id

    def latest(self):
        return _latest_observation(
            self._scan_path, vehicle_id=self._vehicle_id
        )


class AuditedAction:
    def __init__(self, action) -> None:
        self._action = action
        self.goto_location_calls = 0

    def __getattr__(self, name: str):
        target = getattr(self._action, name)
        if name != "goto_location":
            return target

        async def audited_goto(*args, **kwargs):
            self.goto_location_calls += 1
            return await target(*args, **kwargs)

        return audited_goto


class AuditedDrone:
    def __init__(self, drone) -> None:
        self._drone = drone
        self.action = AuditedAction(drone.action)

    def __getattr__(self, name: str):
        return getattr(self._drone, name)


def run_mission_route_scenario(
    artifact_directory: Path,
    *,
    altitude_m: float = 2.0,
    target_north_m: float = 15.0,
    route_timeout_s: float = 25.0,
    startup_wait_s: float = 32.0,
    gui: bool = True,
) -> MissionRouteReport:
    """Run one active static-obstacle mission leg and persist its evidence.

    GUI is the acceptance default: a human can watch the X500, obstacle and
    safety response.  ``gui=False`` exists only for fast unattended regression.
    """
    artifact_directory.mkdir(parents=True, exist_ok=True)
    environment = _simulation_environment(gui=gui)
    launcher = subprocess.Popen(
        (str(_LAUNCHER), "lidar-2d"),
        cwd=_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    scan_capture = None
    pose_capture = None
    scan_path = artifact_directory / ".mission-route-scans.jsonl"
    pose_path = artifact_directory / ".mission-route-pose.jsonl"
    try:
        time.sleep(startup_wait_s)
        _spawn_route_obstacle(environment)
        time.sleep(2.0)
        scan_capture = _stream_topic(scan_topic(), scan_path, environment)
        pose_capture = _stream_topic(_POSE_TOPIC, pose_path, environment)
        time.sleep(2.0)
        report = asyncio.run(
            _fly_route(
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

    destination = artifact_directory / f"mission-route-static-obstacle-{_stamp()}.json"
    destination.write_text(
        json.dumps(report.as_document(), indent=2) + "\n", encoding="utf-8"
    )
    print(f"evidence: {destination}")
    return report


async def _fly_route(
    *,
    altitude_m: float,
    target_north_m: float,
    route_timeout_s: float,
    scan_path: Path,
    pose_path: Path,
) -> MissionRouteReport:
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
    samples: list[MissionRouteSample] = []
    ground_truth_samples: list[MissionGroundTruthSample] = []
    route_started = time.monotonic()
    armed_at_s = 0.0
    ended_at_s = 0.0
    fallback: FallbackRecord | None = None
    landed = False
    arm_attempted = False
    route_reached = False
    execution_error: str | None = None
    control_started_at: float | None = None
    control_ended_at: float | None = None
    ground_truth_stop = asyncio.Event()
    ground_truth_task: asyncio.Task[None] | None = None

    async def monitor_ground_truth() -> None:
        while not ground_truth_stop.is_set():
            clearance, age = _fresh_surface_clearance(pose_path, datetime.now(UTC))
            if clearance is not None and age is not None:
                ground_truth_samples.append(
                    MissionGroundTruthSample(
                        at_s=round(time.monotonic() - route_started, 3),
                        clearance_m=clearance,
                        age_s=age,
                    )
                )
            try:
                await asyncio.wait_for(ground_truth_stop.wait(), timeout=0.2)
            except TimeoutError:
                pass

    def record_tick(tick: RouteTick) -> None:
        clearance, ground_truth_age = _fresh_surface_clearance(
            pose_path, datetime.now(UTC)
        )
        samples.append(
            MissionRouteSample(
                at_s=round(time.monotonic() - route_started, 3),
                nominal_speed_m_s=round(
                    hypot(
                        tick.nominal_velocity.x_m_s,
                        tick.nominal_velocity.y_m_s,
                    ),
                    3,
                ),
                verdict=tick.verdict,
                commanded_speed_m_s=round(
                    hypot(
                        tick.commanded_velocity.x_m_s,
                        tick.commanded_velocity.y_m_s,
                    ),
                    3,
                ),
                delivered=tick.delivered,
                exact_translational_stop=(
                    tick.commanded_velocity.x_m_s
                    == tick.commanded_velocity.y_m_s
                    == tick.commanded_velocity.z_m_s
                    == 0.0
                ),
                clearance_m=clearance,
                ground_truth_age_s=ground_truth_age,
                sensed_distance_m=tick.sensed_distance_m,
            )
        )

    try:
        await _await_armable(drone)
        await drone.action.set_takeoff_altitude(altitude_m)
        ground_truth_task = asyncio.create_task(monitor_ground_truth())
        armed_at_s = time.monotonic() - route_started
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
            tick_observer=record_tick,
        )
        control_started_at = time.monotonic()
        try:
            await executor.run(
                arrival_tolerance_m=1.0, timeout_s=route_timeout_s
            )
        finally:
            control_ended_at = time.monotonic()
        route_reached = True
    except OffboardRouteExecutionError as error:
        fallback = error.fallback
        if error.reason is not RouteTerminalReason.TIMEOUT or fallback is None:
            execution_error = f"{error.reason.value}: {error}"
    except Exception as error:  # preserve evidence, terminate in finally
        execution_error = f"{type(error).__name__}: {error}"
    finally:
        if arm_attempted:
            try:
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
            finally:
                ended_at_s = time.monotonic() - route_started
                ground_truth_stop.set()
                if ground_truth_task is not None:
                    await ground_truth_task

    fallback = fallback or FallbackRecord()
    flown_seconds = max(
        (control_ended_at or time.monotonic())
        - (control_started_at or route_started),
        0.001,
    )
    return evaluate_mission_route_run(
        scenario="static-obstacle",
        samples=samples,
        ground_truth_samples=ground_truth_samples,
        required_clearance_m=scenario_profile.shield.minimum_clearance_m,
        flown_seconds=flown_seconds,
        armed_at_s=armed_at_s,
        ended_at_s=ended_at_s,
        goto_location_calls=drone.action.goto_location_calls,
        fallback_completed=tuple(fallback.completed),
        landed=landed,
        route_reached=route_reached,
        execution_error=execution_error,
        recorded_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


async def _ensure_termination(
    *,
    drone,
    fallback_executor,
    sequence,
    fallback: FallbackRecord | None,
    execution_error: str | None,
    rpc_timeout_s: float = 5.0,
    confirm_timeout_s: float = 30.0,
    retry_confirm_timeout_s: float | None = None,
) -> tuple[FallbackRecord, bool, str | None]:
    record = fallback
    if record is None:
        try:
            record = await asyncio.wait_for(
                fallback_executor.execute(sequence), timeout=rpc_timeout_s
            )
        except Exception as error:
            execution_error = _append_error(
                execution_error, f"fallback executor failed: {error}"
            )
            record = FallbackRecord()
    if "land" not in record.completed:
        try:
            await asyncio.wait_for(drone.action.land(), timeout=rpc_timeout_s)
        except Exception as error:
            execution_error = _append_error(
                execution_error, f"direct emergency land failed: {error}"
            )
        else:
            execution_error = _append_error(
                execution_error,
                "direct emergency land was required after incomplete fallback",
            )
    landed = await _confirm_landed(drone, timeout_s=confirm_timeout_s)
    if not landed:
        execution_error = _append_error(
            execution_error, "landing was not confirmed; direct land retry required"
        )
        try:
            await asyncio.wait_for(drone.action.land(), timeout=rpc_timeout_s)
        except Exception as error:
            execution_error = _append_error(
                execution_error, f"direct land retry failed: {error}"
            )
        landed = await _confirm_landed(
            drone,
            timeout_s=(
                confirm_timeout_s
                if retry_confirm_timeout_s is None
                else retry_confirm_timeout_s
            ),
        )
    return record, landed, execution_error


def _append_error(existing: str | None, detail: str) -> str:
    return f"{existing}; {detail}" if existing else detail


async def _confirm_landed(drone, *, timeout_s: float) -> bool:
    async def wait() -> bool:
        async for in_air in drone.telemetry.in_air():
            if not in_air:
                return True
        return False

    try:
        return await asyncio.wait_for(wait(), timeout=timeout_s)
    except Exception:
        return False


def _canonical_heading(heading_deg: float) -> float:
    if not isfinite(heading_deg):
        raise ValueError("MAVSDK heading must be finite.")
    normalized = (heading_deg + 180.0) % 360.0 - 180.0
    return 180.0 if normalized == -180.0 and heading_deg > 0 else normalized


def _fresh_surface_clearance(
    pose_path: Path, now: datetime, *, max_age_s: float = 0.5
) -> tuple[float | None, float | None]:
    """Point-to-wall-surface clearance with auditable pose freshness."""
    try:
        observed_at = datetime.fromtimestamp(pose_path.stat().st_mtime, tz=UTC)
    except OSError:
        return None, None
    age_s = (now.astimezone(UTC) - observed_at).total_seconds()
    if age_s < 0 or age_s > max_age_s:
        return None, age_s
    message = _last_json_object(pose_path)
    if message is None:
        return None, age_s
    for pose in message.get("pose", []) if isinstance(message, dict) else []:
        if isinstance(pose, dict) and pose.get("name") == _MODEL_NAME:
            position = pose.get("position", {})
            x = float(position.get("x"))
            y = float(position.get("y"))
            # The NED north route maps to Gazebo +Y. The wall is 24 x 2 m,
            # spanning Gazebo X across that route. This is point to
            # axis-aligned wall surface, not the older centre-to-centre metric.
            dx = max(
                abs(x - _ROUTE_OBSTACLE_XY[0])
                - _ROUTE_OBSTACLE_HALF_EXTENTS_XY[0],
                0.0,
            )
            dy = max(
                abs(y - _ROUTE_OBSTACLE_XY[1])
                - _ROUTE_OBSTACLE_HALF_EXTENTS_XY[1],
                0.0,
            )
            return hypot(dx, dy), age_s
    return None, age_s


def _spawn_route_obstacle(environment: dict[str, str]) -> None:
    """Place a 24 m wall across the Gazebo +Y axis used by NED north."""
    sdf = (
        '<sdf version="1.9"><model name="mission_route_obstacle"><static>true</static>'
        f'<pose>{_ROUTE_OBSTACLE_XY[0]} {_ROUTE_OBSTACLE_XY[1]} 1.5 0 0 0</pose>'
        '<link name="l"><collision name="c"><geometry><box>'
        '<size>24 2 3</size></box></geometry></collision>'
        '<visual name="v"><geometry><box><size>24 2 3</size></box>'
        '</geometry></visual></link></model></sdf>'
    )
    result = subprocess.run(
        (
            "gz",
            "service",
            "-s",
            create_service(),
            "--reqtype",
            "gz.msgs.EntityFactory",
            "--reptype",
            "gz.msgs.Boolean",
            "--timeout",
            "5000",
            "--req",
            'name: "mission_route_obstacle", allow_renaming: false, '
            f"sdf: {json.dumps(sdf)}",
        ),
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
        env=environment,
    )
    if "true" not in result.stdout.lower():
        detail = result.stdout.strip() or result.stderr.strip()
        raise RuntimeError(f"Could not spawn the route obstacle: {detail}")


def _simulation_environment(*, gui: bool) -> dict[str, str]:
    environment = {
        **os.environ,
        "GZ_IP": "127.0.0.1",
        "PX4_GZ_WORLD": SCENARIO_WORLD,
    }
    if gui:
        environment["BYTEWOLF_GZ_GUI"] = "1"
    else:
        environment.pop("BYTEWOLF_GZ_GUI", None)
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove the shielded mission route in PX4/Gazebo SITL."
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("simulation/artifacts/control/mission-route"),
    )
    parser.add_argument("--altitude", type=float, default=2.0)
    parser.add_argument("--target-north-metres", type=float, default=15.0)
    parser.add_argument("--route-timeout-seconds", type=float, default=25.0)
    parser.add_argument("--startup-wait-seconds", type=float, default=32.0)
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Fast unattended regression only; GUI acceptance is the default.",
    )
    args = parser.parse_args()
    report = run_mission_route_scenario(
        args.artifact_dir,
        altitude_m=args.altitude,
        target_north_m=args.target_north_metres,
        route_timeout_s=args.route_timeout_seconds,
        startup_wait_s=args.startup_wait_seconds,
        gui=not args.no_gui,
    )
    for finding in report.findings:
        print(f"FAIL: {finding}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
