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
import base64
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from math import atan2, degrees, hypot, sqrt
from pathlib import Path
import subprocess
import time

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.target_estimator import GroundTargetEstimator, NADIR_MONO_CAM_DOWN


# The estimated and true horizontal offset must agree within this, or the sign or
# geometry is wrong. It is generous enough for hover drift and detector centroid
# error, and far tighter than the ~metres a flipped sign would produce.
MATCH_TOLERANCE_M = 1.5

MARKER_RED = ColourTarget(red=220, green=20, blue=20, tolerance=70)
_MODEL_NAME = "x500_mono_cam_down_0"
_IMAGE_TOPIC = f"/world/default/model/{_MODEL_NAME}/link/camera_link/sensor/imager/image"
_POSE_TOPIC = "/world/default/pose/info"
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
        _spawn_marker(environment)
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
        drone = _drone_pose(environment)
        if drone is not None and drone["z"] >= hover_altitude_m - 1.5:
            break
        sleep(1.0)
    else:
        return _blocked_report("The vehicle never reached the hover altitude to capture from.")

    drone = _drone_pose(environment)
    marker = _model_xy(environment, "target_marker")
    if drone is None or marker is None:
        return _blocked_report("Could not read the vehicle or marker pose from Gazebo.")

    frame = _capture_frame(clock(), environment)
    if frame is None:
        return _blocked_report("Could not capture a down-camera frame.")

    adapter = DetectorAdapter(ColourMarkerBackend(MARKER_RED, label="landing-pad"), source="down + colour")
    result = adapter.analyze(frame)
    observation = GroundTargetEstimator(NADIR_MONO_CAM_DOWN, source="down ground-truth").estimate(
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


def _spawn_marker(environment: dict[str, str]) -> None:
    x, y = MARKER_WORLD_XY
    sdf = (
        f'<sdf version="1.9"><model name="target_marker"><static>true</static>'
        f'<pose>{x} {y} 0.02 0 0 0</pose><link name="l">'
        '<visual name="v"><geometry><box><size>1 1 0.04</size></box></geometry>'
        '<material><ambient>0.86 0.08 0.08 1</ambient><diffuse>0.86 0.08 0.08 1</diffuse>'
        '<emissive>0.5 0 0 1</emissive></material></visual>'
        '<collision name="c"><geometry><box><size>1 1 0.04</size></box></geometry></collision>'
        "</link></model></sdf>"
    )
    subprocess.run(
        (
            "gz", "service", "-s", "/world/default/create",
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", f'name: "target_marker", allow_renaming: false, sdf: {json.dumps(sdf)}',
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )


def _capture_frame(captured_at: datetime, environment: dict[str, str]) -> CameraFrame | None:
    message = _one_message(_IMAGE_TOPIC, environment, timeout=15.0)
    if message is None:
        return None
    try:
        width = int(message["width"])
        height = int(message["height"])
        data = base64.b64decode(message["data"])
    except (KeyError, ValueError, TypeError):
        return None
    if len(data) != width * height * 3:
        return None
    return CameraFrame(
        sensor_id="down_rgb", encoding=FrameEncoding.RGB8, width=width, height=height,
        data=data, captured_at=captured_at, frame_id="down-ground-truth",
    )


def _drone_pose(environment: dict[str, str]) -> dict | None:
    message = _one_message(_POSE_TOPIC, environment, timeout=10.0)
    for pose in (message or {}).get("pose", []):
        if pose.get("name") == _MODEL_NAME:
            position = pose.get("position", {})
            orientation = pose.get("orientation", {})
            qx, qy, qz, qw = (orientation.get(k, 0.0) for k in ("x", "y", "z", "w"))
            yaw = degrees(atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)))
            tilt = degrees(2 * atan2(sqrt(qx * qx + qy * qy), sqrt(qz * qz + qw * qw)))
            tilt = min(tilt, 180 - tilt)
            return {
                "x": float(position.get("x", 0.0)),
                "y": float(position.get("y", 0.0)),
                "z": float(position.get("z", 0.0)),
                "yaw_deg": yaw,
                "tilt_deg": abs(tilt),
            }
    return None


def _model_xy(environment: dict[str, str], name: str) -> tuple[float, float] | None:
    message = _one_message(_POSE_TOPIC, environment, timeout=10.0)
    for pose in (message or {}).get("pose", []):
        if pose.get("name") == name:
            position = pose.get("position", {})
            return float(position.get("x", 0.0)), float(position.get("y", 0.0))
    return None


def _one_message(topic: str, environment: dict[str, str], *, timeout: float) -> dict | None:
    try:
        completed = subprocess.run(
            ("gz", "topic", "-e", "-t", topic, "--json-output", "-n", "1"),
            capture_output=True, text=True, timeout=timeout, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


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
