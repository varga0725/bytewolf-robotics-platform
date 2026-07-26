"""Measure whether PX4 Collision Prevention shields the mission path. It does not.

Gate G3 asks whether the project needs its own runtime shield, and answers it by
measuring the PX4 baseline's known limits rather than trusting them. PX4
Collision Prevention runs only in Position mode; the project flies goto_location
missions in Auto/Hold. This scenario puts that to the test: it enables CP, flies
a mission straight at a known obstacle, and records how close the vehicle got.

If CP were shielding the flight, the vehicle would hold at roughly ``CP_DIST``
from the obstacle. On the mission path it flies right up to it, and that closest
approach -- measured from Gazebo ground truth, not from the flight stack -- is
the evidence that G3 is already decided: the mission-path shield has to come from
somewhere else (the Offboard + CBF phases), because the PX4 baseline does nothing
here.

The scoring is a pure function of the captured pose track and the placed
obstacle, so it is unit-tested without SITL; only :func:`run_cp_baseline`
touches Gazebo and MAVSDK.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from math import hypot, isfinite
from pathlib import Path
import subprocess
import sys
import time


# The obstacle box is 1 m wide, so its surface is half a metre from its centre.
OBSTACLE_HALF_WIDTH_M = 0.5

# The vehicle's controller overshoots and the clearance is measured to the box
# centre, so CP only counts as intervening if it held well outside CP_DIST.
INTERVENTION_MARGIN_M = 1.5

# Below this clearance the vehicle clearly entered the zone CP claims to protect,
# so the measurement is conclusive rather than a flight that never approached.
CONCLUSIVE_CLEARANCE_M = 2.0


@dataclass(frozen=True)
class CollisionPreventionBaselineReport:
    """What the mission-path flight measured against an enabled CP shield."""

    cp_dist_m: float
    obstacle_xy_m: list[float]
    obstacle_half_width_m: float
    pose_samples: int
    min_clearance_m: float
    approached_obstacle: bool
    cp_intervened: bool
    verdict: str
    detail: str

    @property
    def measured(self) -> bool:
        return self.verdict == "measured"


def evaluate_cp_baseline(
    pose_track_xy: Sequence[tuple[float, float]],
    obstacle_xy: tuple[float, float],
    cp_dist_m: float,
    *,
    obstacle_half_width_m: float = OBSTACLE_HALF_WIDTH_M,
) -> CollisionPreventionBaselineReport:
    """Score how close a mission-path flight came to a known obstacle."""
    if cp_dist_m <= 0.0:
        raise ValueError("CP_DIST must be positive for the baseline to mean anything.")
    clearances = [
        hypot(x - obstacle_xy[0], y - obstacle_xy[1]) - obstacle_half_width_m
        for x, y in pose_track_xy
        if isfinite(x) and isfinite(y)
    ]
    if not clearances:
        return _inconclusive(cp_dist_m, obstacle_xy, obstacle_half_width_m, 0, "No vehicle pose was captured.")

    min_clearance = min(clearances)
    approached = min_clearance < cp_dist_m + CONCLUSIVE_CLEARANCE_M
    intervened = min_clearance >= cp_dist_m - INTERVENTION_MARGIN_M
    if not approached:
        return _inconclusive(
            cp_dist_m, obstacle_xy, obstacle_half_width_m, len(clearances),
            f"The vehicle never came within test range: closest approach {min_clearance:.2f} m.",
        )

    detail = (
        f"CP_DIST was {cp_dist_m:.1f} m; the vehicle closed to {min_clearance:.2f} m on the mission path. "
        + (
            "Collision Prevention held its distance."
            if intervened
            else "Collision Prevention did not intervene -- the mission path is unshielded, as expected in Auto/Hold."
        )
    )
    return CollisionPreventionBaselineReport(
        cp_dist_m=cp_dist_m,
        obstacle_xy_m=[obstacle_xy[0], obstacle_xy[1]],
        obstacle_half_width_m=obstacle_half_width_m,
        pose_samples=len(clearances),
        min_clearance_m=round(min_clearance, 3),
        approached_obstacle=True,
        cp_intervened=intervened,
        verdict="measured",
        detail=detail,
    )


def _inconclusive(
    cp_dist_m: float, obstacle_xy: tuple[float, float], half_width: float, samples: int, reason: str
) -> CollisionPreventionBaselineReport:
    return CollisionPreventionBaselineReport(
        cp_dist_m=cp_dist_m,
        obstacle_xy_m=[obstacle_xy[0], obstacle_xy[1]],
        obstacle_half_width_m=half_width,
        pose_samples=samples,
        min_clearance_m=float("inf"),
        approached_obstacle=False,
        cp_intervened=False,
        verdict="inconclusive",
        detail=reason,
    )


def pose_track_from_stream(lines: Sequence[str], model_name: str) -> list[tuple[float, float]]:
    """Extract one model's horizontal track from a captured gz pose stream."""
    track: list[tuple[float, float]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        for pose in document.get("pose", []) if isinstance(document, dict) else []:
            if isinstance(pose, dict) and pose.get("name") == model_name:
                position = pose.get("position", {})
                track.append((float(position.get("x", 0.0)), float(position.get("y", 0.0))))
    return track


_LAUNCHER = Path("simulation/gazebo/launch/run_px4_gazebo_headless.zsh")
_POSE_TOPIC = "/world/default/pose/info"
_MODEL_NAME = "x500_lidar_2d_0"
# The obstacle sits due north in the flight path; the vehicle is told to fly past
# it, so an unshielded flight closes to nearly zero clearance.
OBSTACLE_NORTH_M = 10.0
WAYPOINT_NORTH_M = 14.0
CP_DIST_M = 5.0


def run_cp_baseline(
    output_directory: Path,
    *,
    startup_wait_s: float = 32.0,
    project_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Enable CP, fly a mission straight at an obstacle, and record the clearance."""
    import os

    root = project_root or Path(__file__).resolve().parents[2]
    clock = now or (lambda: datetime.now(UTC))
    environment = {**os.environ, "GZ_IP": "127.0.0.1"}
    obstacle_xy = (0.0, OBSTACLE_NORTH_M)

    launcher = subprocess.Popen(
        (str(_LAUNCHER), "lidar-2d"), cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pose_capture: subprocess.Popen | None = None
    pose_path = output_directory / "cp-baseline-pose-track.jsonl"
    output_directory.mkdir(parents=True, exist_ok=True)
    try:
        sleep(startup_wait_s)
        _enable_collision_prevention(root, environment)
        _spawn_obstacle(obstacle_xy, environment)
        sleep(2.0)
        pose_stream = pose_path.open("w", encoding="utf-8")
        pose_capture = subprocess.Popen(
            ("gz", "topic", "-e", "-t", _POSE_TOPIC, "--json-output"),
            stdout=pose_stream, stderr=subprocess.DEVNULL, env=environment,
        )
        _fly_toward_obstacle(root)
        pose_capture.terminate()
        pose_capture.wait(timeout=10.0)
        pose_stream.close()
        track = pose_track_from_stream(pose_path.read_text(encoding="utf-8").splitlines(), _MODEL_NAME)
        report = evaluate_cp_baseline(track, obstacle_xy, CP_DIST_M)
    finally:
        if pose_capture is not None and pose_capture.poll() is None:
            pose_capture.kill()
        launcher.terminate()
        try:
            launcher.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            launcher.kill()
        pose_path.unlink(missing_ok=True)
    return write_report(report, output_directory, now=clock)


def _enable_collision_prevention(root: Path, environment: dict[str, str]) -> None:
    from simulation.gazebo.fault_injection import apply_px4_parameters

    # CP_DIST > 0 turns Collision Prevention on; if it ever shields Auto/Hold this
    # is what would make the vehicle stop. MPC caps keep the approach gentle.
    apply_px4_parameters(
        (("CP_DIST", CP_DIST_M), ("MPC_XY_VEL_MAX", 3.0)),
        px4_build_directory=root / "PX4-Autopilot/build/px4_sitl_default",
    )


def _spawn_obstacle(obstacle_xy: tuple[float, float], environment: dict[str, str]) -> None:
    sdf = (
        f'<sdf version="1.9"><model name="obstacle_north"><static>true</static>'
        f'<pose>{obstacle_xy[0]} {obstacle_xy[1]} 1 0 0 0</pose><link name="l">'
        '<collision name="c"><geometry><box><size>1 1 2</size></box></geometry></collision>'
        '<visual name="v"><geometry><box><size>1 1 2</size></box></geometry></visual>'
        "</link></model></sdf>"
    )
    result = subprocess.run(
        (
            "gz", "service", "-s", "/world/default/create",
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", f'name: "obstacle_north", allow_renaming: false, sdf: {json.dumps(sdf)}',
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )
    if "true" not in result.stdout.lower():
        raise RuntimeError(f"Could not spawn the obstacle: {result.stdout.strip() or result.stderr.strip()}")


def _fly_toward_obstacle(root: Path) -> None:
    subprocess.run(
        (
            sys.executable, "-m", "brain.cli.fly_waypoint_land",
            "--north", str(WAYPOINT_NORTH_M), "--east", "0",
            "--takeoff-altitude", "2", "--waypoint-altitude", "2",
            "--hover-seconds", "2", "--waypoint-timeout", "40",
            "--preflight-wait-seconds", "60",
            "--artifact-dir", str(root / "simulation/artifacts/perception/cp-baseline-mission"),
        ),
        cwd=root, capture_output=True, text=True, timeout=180.0, check=False,
    )


def write_report(
    report: CollisionPreventionBaselineReport, output_directory: Path, *, now: Callable[[], datetime]
) -> Path:
    """Persist one CP baseline measurement as a durable artifact."""
    timestamp = now().astimezone(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"cp-baseline-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(
        json.dumps(
            {
                "started_at": timestamp.isoformat().replace("+00:00", "Z"),
                "verification_level": "px4-gazebo-fault-injection",
                "flight_mode": "auto/hold (goto_location)",
                "note": "PX4 Collision Prevention runs only in Position mode; this measures the mission path.",
                **asdict(report),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def main(arguments: tuple[str, ...] | None = None) -> int:
    """Run the CP baseline measurement and print the artifact path."""
    parser = argparse.ArgumentParser(
        description="Measure whether PX4 Collision Prevention shields the goto_location mission path."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("simulation/artifacts/perception"),
        help="Where to write the CP baseline artifact.",
    )
    parsed = parser.parse_args(arguments)
    report_path = run_cp_baseline(parsed.output_dir)
    document = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"CP baseline: {document['verdict']} — {report_path}")
    print(f"  {document['detail']}")
    return 0 if document["verdict"] == "measured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
