"""Fly the autonomous "see it and go to it" demo against real SITL, end to end.

This is the whole V1 perception loop closed over a live vehicle: the drone takes
off, the down camera sees a red marker, the estimator projects it to a ground
position, the reaction proposes a move, the SafetyGate approves it, and the
vehicle flies there and lands -- with the arrival confirmed against Gazebo's own
ground truth. Nothing here writes an actuator command: perception hands the
adapter a gate-approved ``WaypointCommand`` (or nothing), and PX4 flies it.

The scoring is a pure function of the final offset, unit-tested without SITL. The
runner is what touches Gazebo and MAVSDK; a run that never sees the marker, that
the gate refuses, or that cannot read a pose fails closed to a non-"reached"
verdict rather than claiming success. The move the vehicle makes is discovered
live and re-checked by the gate at that moment, so this demo cannot fly to a
target the safety layer would not approve.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from math import hypot
import os
from pathlib import Path
import subprocess
import time

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.commands import WaypointCommand
from brain.mission.flight import authorize_takeoff_target_approach_land
from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.target_approach import ApproachDecision, plan_target_approach
from brain.perception.target_estimator import CameraIntrinsics, GroundTargetEstimator
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from simulation.perception import gz_scene
from simulation.perception.gz_scene import MONO_CAM_HORIZONTAL_FOV_RAD


# The estimated arrival must land the vehicle this close to the marker, or the
# geometry or the flight did not deliver it. It is generous for hover drift and
# centroid error, far tighter than a flipped-sign miss would produce.
ARRIVAL_TOLERANCE_M = 2.0

MARKER_RED = ColourTarget(red=220, green=20, blue=20, tolerance=70)
_MODEL_NAME = "x500_mono_cam_down_0"
_IMAGE_TOPIC = gz_scene.image_topic(_MODEL_NAME)
_LAUNCHER = Path("simulation/gazebo/launch/run_px4_gazebo_headless.zsh")
# World +X is east, +Y is north; the marker sits off both axes so the sign of the
# whole loop -- estimate, waypoint, and flight -- is tested, not just distance.
MARKER_WORLD_XY = (2.0, 3.0)


@dataclass(frozen=True)
class ApproachReport:
    """The full story of one autonomous approach: seen, approved, and reached."""

    target_detected: bool
    move_approved: bool
    refusal_reason: str | None
    final_offset_to_marker_m: float | None
    tolerance_m: float
    verdict: str
    detail: str

    @property
    def reached(self) -> bool:
        return self.verdict == "reached"


def evaluate_approach(
    *,
    target_detected: bool,
    move_approved: bool,
    refusal_reason: str | None,
    final_north_m: float | None,
    final_east_m: float | None,
    marker_north_m: float | None,
    marker_east_m: float | None,
    tolerance_m: float = ARRIVAL_TOLERANCE_M,
) -> ApproachReport:
    """Score whether the vehicle ended up over the marker it was approved to reach.

    A run that proposed no move (nothing seen, or the gate refused) is a valid,
    safe outcome, not a match: the vehicle correctly stayed put. Only an approved
    move that ends within tolerance of the marker is a "reached".
    """
    if not move_approved:
        return ApproachReport(
            target_detected=target_detected, move_approved=False, refusal_reason=refusal_reason,
            final_offset_to_marker_m=None, tolerance_m=tolerance_m, verdict="no_move",
            detail=(
                "No move was proposed, so the vehicle stayed put. "
                + (refusal_reason or "No trustworthy target was seen.")
            ),
        )
    if None in (final_north_m, final_east_m, marker_north_m, marker_east_m):
        return ApproachReport(
            target_detected=target_detected, move_approved=True, refusal_reason=None,
            final_offset_to_marker_m=None, tolerance_m=tolerance_m, verdict="blocked",
            detail="A move was flown but the final vehicle or marker pose could not be read.",
        )
    offset = hypot(final_north_m - marker_north_m, final_east_m - marker_east_m)
    reached = offset <= tolerance_m
    return ApproachReport(
        target_detected=target_detected, move_approved=True, refusal_reason=None,
        final_offset_to_marker_m=round(offset, 3), tolerance_m=tolerance_m,
        verdict="reached" if reached else "missed",
        detail=(
            f"The vehicle finished {offset:.2f} m from the marker (tolerance {tolerance_m:.2f} m)."
            + ("" if reached else " The approach did not deliver it over the target.")
        ),
    )


def _blocked_report(reason: str, *, detected: bool = False) -> ApproachReport:
    return ApproachReport(
        target_detected=detected, move_approved=False, refusal_reason=None,
        final_offset_to_marker_m=None, tolerance_m=ARRIVAL_TOLERANCE_M,
        verdict="blocked", detail=reason,
    )


async def run_autonomous_approach_scenario(
    output_directory: Path,
    *,
    capture_altitude_m: float = 8.0,
    approach_altitude_m: float = 8.0,
    startup_wait_s: float = 32.0,
    connection_timeout_s: float = 20.0,
    mavsdk_server_port: int = 50055,
    project_root: Path | None = None,
) -> Path:
    """Fly the down-camera SITL, let it find and approach the marker, and score it."""
    root = project_root or Path(__file__).resolve().parents[2]
    environment = {**os.environ, "GZ_IP": "127.0.0.1"}
    clock = lambda: datetime.now(UTC)

    launcher = subprocess.Popen(
        (str(_LAUNCHER), "mono-down"), cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    report = _blocked_report("The scenario did not run to a verdict.")
    try:
        await asyncio.to_thread(time.sleep, startup_wait_s)
        gz_scene.spawn_marker(environment, xy=MARKER_WORLD_XY)
        report = await _fly_and_score(
            environment, clock,
            capture_altitude_m=capture_altitude_m,
            approach_altitude_m=approach_altitude_m,
            connection_timeout_s=connection_timeout_s,
            mavsdk_server_port=mavsdk_server_port,
            output_directory=output_directory,
        )
    finally:
        launcher.terminate()
        try:
            launcher.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            launcher.kill()
    return _write_report(report, output_directory, clock)


async def _fly_and_score(
    environment, clock, *, capture_altitude_m, approach_altitude_m,
    connection_timeout_s, mavsdk_server_port, output_directory,
) -> ApproachReport:
    from mavsdk import System

    from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server

    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    gate = SafetyGate(profile.flight_limits())
    detector = DetectorAdapter(ColourMarkerBackend(MARKER_RED, label="landing-pad"), source="down + colour")
    # The decision made in flight is stored here for the report; the closure below
    # is the perception seam the adapter calls once the vehicle is airborne.
    captured: dict[str, ApproachDecision] = {}

    async def propose_move() -> WaypointCommand | None:
        # Wait for the climb to near the capture altitude before looking: a frame
        # taken low sees a narrow ground patch and the marker falls outside it.
        drone = None
        for _ in range(40):
            drone = await asyncio.to_thread(gz_scene.drone_pose, environment, _MODEL_NAME)
            if drone is not None and drone["z"] >= capture_altitude_m - 1.5:
                break
            await asyncio.sleep(1.0)
        if drone is None or drone["z"] < capture_altitude_m - 1.5:
            return None
        frame = await asyncio.to_thread(
            gz_scene.capture_frame, clock(), environment,
            topic=_IMAGE_TOPIC, sensor_id="down_rgb", frame_id="down-approach",
        )
        if drone is None or frame is None:
            return None
        estimator = GroundTargetEstimator(
            CameraIntrinsics(frame.width, frame.height, MONO_CAM_HORIZONTAL_FOV_RAD),
            source="down approach",
        )
        decision = plan_target_approach(
            frame, detector=detector, estimator=estimator, gate=gate,
            altitude_agl_m=drone["z"], vehicle_north_m=drone["y"], vehicle_east_m=drone["x"],
            now=clock(), approach_altitude_m=approach_altitude_m,
            yaw_deg=drone["yaw_deg"], tilt_deg=drone["tilt_deg"],
        )
        captured["decision"] = decision
        return decision.waypoint

    mission = authorize_takeoff_target_approach_land(
        gate, takeoff_altitude_m=capture_altitude_m, hover_duration_s=6.0,
        waypoint_tolerance_m=ARRIVAL_TOLERANCE_M,
    )

    system = System(port=mavsdk_server_port)
    adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=60.0)
    try:
        await asyncio.wait_for(adapter.connect("udpin://0.0.0.0:14540"), timeout=connection_timeout_s)
        await adapter.execute_target_approach_mission(mission, propose_move)
    except Exception as error:  # noqa: BLE001 - the run is scored, not crashed
        decision = captured.get("decision")
        return _blocked_report(
            f"The approach mission did not complete: {type(error).__name__}: {error}",
            detected=decision is not None and decision.observation.state(datetime.now(UTC)).usable,
        )
    finally:
        stop_owned_mavsdk_server(system)

    # The vehicle has landed; read where it ended up against the marker.
    final = gz_scene.drone_pose(environment, _MODEL_NAME)
    marker = gz_scene.model_xy(environment, "target_marker")
    decision = captured.get("decision")
    detected = decision is not None and decision.observation.declared_validity == "valid"
    return evaluate_approach(
        target_detected=detected,
        move_approved=decision is not None and decision.accepted,
        refusal_reason=None if decision is None else decision.refusal_reason,
        final_north_m=None if final is None else final["y"],
        final_east_m=None if final is None else final["x"],
        marker_north_m=None if marker is None else marker[1],
        marker_east_m=None if marker is None else marker[0],
    )


def _write_report(report: ApproachReport, output_directory: Path, now) -> Path:
    timestamp = now().astimezone(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"autonomous-approach-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(
        json.dumps(
            {
                "started_at": timestamp.isoformat().replace("+00:00", "Z"),
                "verification_level": "app-sitl",
                "sensor": "gz_x500_mono_cam_down",
                "marker_world_xy_m": list(MARKER_WORLD_XY),
                **asdict(report),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def main(arguments: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fly the autonomous see-it-and-go-to-it demo on SITL.")
    parser.add_argument("--output-dir", type=Path, default=Path("simulation/artifacts/perception"))
    parsed = parser.parse_args(arguments)
    report_path = asyncio.run(run_autonomous_approach_scenario(parsed.output_dir))
    document = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"Autonomous approach: {document['verdict']} — {report_path}")
    print(f"  {document['detail']}")
    return 0 if document["verdict"] == "reached" else 1


if __name__ == "__main__":
    raise SystemExit(main())
