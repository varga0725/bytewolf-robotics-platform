"""Confirm the down-camera target estimator against Gazebo ground truth.

The estimator's north/east sign is only unit-checked for internal consistency;
this pins it against reality. It flies a real x500_mono_cam_down, places a red
marker at a known spot, and runs the whole perception path on a real captured
frame -- colour detector, detector adapter, ground target estimator -- then
compares the estimated offset to the true offset between the vehicle and the
marker, both read from Gazebo. If the sign or the geometry were wrong, the
estimate would land on the opposite side or the wrong distance, and the run
fails rather than passing.

The comparison is a pure function of the two offsets, unit-tested without SITL;
only the runner touches Gazebo and the flight stack. A run that cannot see the
marker, or that reads no pose, fails closed rather than claiming a match.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from math import hypot
from pathlib import Path
import subprocess
import time

from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.target_estimator import CameraIntrinsics, GroundTargetEstimator
from simulation.perception import gz_scene
from simulation.perception.gz_scene import MONO_CAM_HORIZONTAL_FOV_RAD


# The estimated and true horizontal offset must agree within this, or the sign or
# geometry is wrong. It is generous enough for hover drift and detector centroid
# error, and far tighter than the ~metres a flipped sign would produce.
MATCH_TOLERANCE_M = 1.5

MARKER_RED = ColourTarget(red=220, green=20, blue=20, tolerance=70)
_MODEL_NAME = "x500_mono_cam_down_0"
_IMAGE_TOPIC = gz_scene.image_topic(_MODEL_NAME)
_LAUNCHER = Path("simulation/gazebo/launch/run_px4_gazebo_headless.zsh")
# World +X is east, +Y is north; the marker sits off both axes so the sign of
# each is tested, not just the magnitude.
MARKER_WORLD_XY = (1.0, 2.0)


@dataclass(frozen=True)
class GroundTruthReport:
    """How the estimated target offset compared with the true one."""

    estimated_north_m: float | None
    estimated_east_m: float | None
    ground_truth_north_m: float | None
    ground_truth_east_m: float | None
    error_m: float | None
    tolerance_m: float
    altitude_m: float | None
    verdict: str
    detail: str

    @property
    def matched(self) -> bool:
        return self.verdict == "matched"


def evaluate_ground_truth(
    estimated_north_m: float,
    estimated_east_m: float,
    ground_truth_north_m: float,
    ground_truth_east_m: float,
    *,
    altitude_m: float,
    tolerance_m: float = MATCH_TOLERANCE_M,
) -> GroundTruthReport:
    """Score an estimated offset against the true vehicle-to-marker offset."""
    error = hypot(estimated_north_m - ground_truth_north_m, estimated_east_m - ground_truth_east_m)
    matched = error <= tolerance_m
    return GroundTruthReport(
        estimated_north_m=round(estimated_north_m, 3),
        estimated_east_m=round(estimated_east_m, 3),
        ground_truth_north_m=round(ground_truth_north_m, 3),
        ground_truth_east_m=round(ground_truth_east_m, 3),
        error_m=round(error, 3),
        tolerance_m=tolerance_m,
        altitude_m=round(altitude_m, 3),
        verdict="matched" if matched else "mismatched",
        detail=(
            f"Estimated ({estimated_north_m:.2f} N, {estimated_east_m:.2f} E) against true "
            f"({ground_truth_north_m:.2f} N, {ground_truth_east_m:.2f} E); error {error:.2f} m "
            f"(tolerance {tolerance_m:.2f} m)."
            + ("" if matched else " The estimator's geometry disagrees with ground truth.")
        ),
    )


def _blocked_report(reason: str, tolerance_m: float = MATCH_TOLERANCE_M) -> GroundTruthReport:
    return GroundTruthReport(
        estimated_north_m=None, estimated_east_m=None,
        ground_truth_north_m=None, ground_truth_east_m=None,
        error_m=None, tolerance_m=tolerance_m, altitude_m=None,
        verdict="blocked", detail=reason,
    )


def run_ground_truth_scenario(
    output_directory: Path,
    *,
    hover_altitude_m: float = 8.0,
    startup_wait_s: float = 32.0,
    project_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Fly the down-camera SITL over a known marker and check the estimator."""
    import os

    root = project_root or Path(__file__).resolve().parents[2]
    clock = now or (lambda: datetime.now(UTC))
    environment = {**os.environ, "GZ_IP": "127.0.0.1"}

    launcher = subprocess.Popen(
        (str(_LAUNCHER), "mono-down"), cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    mission: subprocess.Popen | None = None
    try:
        sleep(startup_wait_s)
        gz_scene.spawn_marker(environment, xy=MARKER_WORLD_XY)
        mission = subprocess.Popen(
            (
                _python(), "-m", "brain.cli.fly_takeoff_hover_land",
                "--altitude", str(hover_altitude_m), "--hover-seconds", "18",
                "--preflight-wait-seconds", "60",
                "--artifact-dir", str(output_directory / "ground-truth-mission"),
            ),
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        report = _capture_and_evaluate(hover_altitude_m, environment, clock, sleep)
    finally:
        if mission is not None and mission.poll() is None:
            mission.terminate()
        launcher.terminate()
        try:
            launcher.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            launcher.kill()
    return _write_report(report, output_directory, clock)


def _capture_and_evaluate(hover_altitude_m, environment, clock, sleep) -> GroundTruthReport:
    # Wait for the vehicle to climb near the hover altitude before capturing.
    for _ in range(40):
        drone = gz_scene.drone_pose(environment, _MODEL_NAME)
        if drone is not None and drone["z"] >= hover_altitude_m - 1.5:
            break
        sleep(1.0)
    else:
        return _blocked_report("The vehicle never reached the hover altitude to capture from.")

    drone = gz_scene.drone_pose(environment, _MODEL_NAME)
    marker = gz_scene.model_xy(environment, "target_marker")
    if drone is None or marker is None:
        return _blocked_report("Could not read the vehicle or marker pose from Gazebo.")

    frame = gz_scene.capture_frame(
        clock(), environment, topic=_IMAGE_TOPIC, sensor_id="down_rgb", frame_id="down-ground-truth"
    )
    if frame is None:
        return _blocked_report("Could not capture a down-camera frame.")

    adapter = DetectorAdapter(ColourMarkerBackend(MARKER_RED, label="landing-pad"), source="down + colour")
    result = adapter.analyze(frame)
    intrinsics = CameraIntrinsics(frame.width, frame.height, MONO_CAM_HORIZONTAL_FOV_RAD)
    observation = GroundTargetEstimator(intrinsics, source="down ground-truth").estimate(
        result, altitude_agl_m=drone["z"], now=clock(), yaw_deg=drone["yaw_deg"], tilt_deg=drone["tilt_deg"]
    )
    if not observation.state(clock()).usable:
        return _blocked_report(
            f"The pipeline produced no usable target ({observation.declared_validity}); "
            "the marker may not have been seen."
        )

    # Gazebo world is ENU: north is +Y, east is +X.
    gt_north = marker[1] - drone["y"]
    gt_east = marker[0] - drone["x"]
    return evaluate_ground_truth(
        observation.offset_north_m, observation.offset_east_m, gt_north, gt_east, altitude_m=drone["z"]
    )


def _write_report(report: GroundTruthReport, output_directory: Path, now: Callable[[], datetime]) -> Path:
    timestamp = now().astimezone(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"target-ground-truth-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(
        json.dumps(
            {
                "started_at": timestamp.isoformat().replace("+00:00", "Z"),
                "verification_level": "px4-gazebo-fault-injection",
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


def _python() -> str:
    import sys

    return sys.executable


def main(arguments: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confirm the down-camera target estimator against ground truth.")
    parser.add_argument("--output-dir", type=Path, default=Path("simulation/artifacts/perception"))
    parsed = parser.parse_args(arguments)
    report_path = run_ground_truth_scenario(parsed.output_dir)
    document = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"Target ground truth: {document['verdict']} — {report_path}")
    print(f"  {document['detail']}")
    return 0 if document["verdict"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
