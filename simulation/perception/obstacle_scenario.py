"""Measure the lidar obstacle path against a known obstacle, and record it.

This is the evidence half of the perception path: it drives a real
``gz_x500_lidar_2d`` SITL, places one obstacle at a known bearing, and reports
what the adapter actually saw over many scans. The verdict is not "an obstacle
appeared once" but "the sensor reported the known obstacle on the right sector,
kept the blind spot unobserved, and did so on nearly every scan".

The scoring is a pure function of the captured scans and the ground-truth
obstacle, so it is unit-tested without SITL; only :func:`run_obstacle_scenario`
touches Gazebo. A scenario that cannot confirm its obstacle fails closed rather
than recording a pass, the same discipline the wind and fault evidence follow.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import time

from brain.memory.recorder import DEFAULT_WORLD_MEMORY_PATH, RecordingResult, WorldMemoryRecorder
from brain.memory.world_map import MapGrid, VehiclePose
from brain.perception.lidar_obstacle import (
    LidarObstacleError,
    laser_scan_from_gz_json,
    obstacle_observation,
)
from brain.telemetry.observation import load_observation


# The gz_x500_lidar_2d sees 270 degrees; a bearing this far off forward is behind
# the vehicle and must never come back as anything but unobserved.
BLIND_SPOT_MIN_ABS_YAW_DEG = 140.0

# A scenario must detect its known obstacle on at least this fraction of scans.
DEFAULT_MIN_DETECTION_RATE = 0.9


@dataclass(frozen=True)
class ExpectedObstacle:
    """The ground truth a scenario places and then checks the sensor against."""

    sector_yaw_deg: float
    distance_m: float
    distance_tolerance_m: float = 0.5


@dataclass(frozen=True)
class ObstacleScenarioReport:
    """An immutable record of what the obstacle path saw over a run."""

    scans: int
    detections: int
    detection_rate: float
    false_negative_rate: float
    min_detection_rate: float
    expected: dict
    measured_distances_m: list[float]
    blind_spot_always_unobserved: bool
    verdict: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.verdict == "passed"


def evaluate_obstacle_scenario(
    observations: Sequence[dict],
    expected: ExpectedObstacle,
    *,
    min_detection_rate: float = DEFAULT_MIN_DETECTION_RATE,
) -> ObstacleScenarioReport:
    """Score captured obstacle observations against the obstacle that was placed."""
    if not observations:
        return _blocked_report(expected, min_detection_rate, "No scans were captured.")

    detections = 0
    measured_distances: list[float] = []
    blind_spot_ok = True
    for document in observations:
        sectors = document["payload"]["sectors"]
        detected, distance = _detection_in_expected_sector(sectors, expected)
        if detected:
            detections += 1
            measured_distances.append(distance)
        if not _blind_spot_unobserved(sectors):
            blind_spot_ok = False

    scans = len(observations)
    detection_rate = detections / scans
    distance_ok = all(
        abs(distance - expected.distance_m) <= expected.distance_tolerance_m for distance in measured_distances
    )
    passed = detection_rate >= min_detection_rate and blind_spot_ok and bool(measured_distances) and distance_ok
    return ObstacleScenarioReport(
        scans=scans,
        detections=detections,
        detection_rate=round(detection_rate, 4),
        false_negative_rate=round(1.0 - detection_rate, 4),
        min_detection_rate=min_detection_rate,
        expected=asdict(expected),
        measured_distances_m=[round(distance, 3) for distance in measured_distances],
        blind_spot_always_unobserved=blind_spot_ok,
        verdict="passed" if passed else "failed",
        detail=_verdict_detail(detection_rate, min_detection_rate, blind_spot_ok, measured_distances, distance_ok, expected),
    )


def _detection_in_expected_sector(sectors: Iterable[dict], expected: ExpectedObstacle) -> tuple[bool, float]:
    for sector in sectors:
        if sector["yaw_deg"] == expected.sector_yaw_deg and sector["coverage"] == "measured":
            return True, float(sector["distance_m"])
    return False, 0.0


def _blind_spot_unobserved(sectors: Iterable[dict]) -> bool:
    return all(
        sector["coverage"] == "unobserved"
        for sector in sectors
        if abs(sector["yaw_deg"]) >= BLIND_SPOT_MIN_ABS_YAW_DEG
    )


def _verdict_detail(
    detection_rate: float,
    min_detection_rate: float,
    blind_spot_ok: bool,
    measured_distances: Sequence[float],
    distance_ok: bool,
    expected: ExpectedObstacle,
) -> str:
    if not measured_distances:
        return "The known obstacle was never detected on its expected sector."
    parts = [f"Detected on {detection_rate:.0%} of scans (needs {min_detection_rate:.0%})."]
    if not blind_spot_ok:
        parts.append("The rear blind spot reported coverage it cannot have.")
    if not distance_ok:
        nearest = min(measured_distances)
        parts.append(
            f"Measured distance {nearest:.2f} m is outside {expected.distance_tolerance_m:.2f} m "
            f"of the placed {expected.distance_m:.2f} m."
        )
    return " ".join(parts)


def _blocked_report(expected: ExpectedObstacle, min_detection_rate: float, reason: str) -> ObstacleScenarioReport:
    return ObstacleScenarioReport(
        scans=0,
        detections=0,
        detection_rate=0.0,
        false_negative_rate=1.0,
        min_detection_rate=min_detection_rate,
        expected=asdict(expected),
        measured_distances_m=[],
        blind_spot_always_unobserved=False,
        verdict="failed",
        detail=reason,
    )


def observations_from_scans(
    scan_messages: Iterable[dict], *, vehicle_id: str, sensor_id: str, now: Callable[[], datetime]
) -> list[dict]:
    """Convert captured gz scans into contract-valid obstacle observations.

    Each document is passed through the contract loader, so a scan that produces
    an invalid observation stops the run instead of being scored.
    """
    observations = []
    for message in scan_messages:
        document = obstacle_observation(
            laser_scan_from_gz_json(message), vehicle_id=vehicle_id, observed_at=now(), sensor_id=sensor_id
        )
        load_observation(document)
        observations.append(document)
    return observations


def remember_scanned_world(
    observation_documents: Sequence[dict],
    recorder: WorldMemoryRecorder,
    *,
    pose: VehiclePose,
    grid: MapGrid,
    now: datetime,
    artifact: str | None = None,
) -> RecordingResult:
    """Remember the freshest scan of a run, not every scan of it.

    Thirty scans of one wall are one fact about that wall observed thirty
    times. Writing all of them would bury the log in duplicates that the
    contradiction rules then have to re-resolve on every read, so only the last
    scan — the freshest evidence — is recorded.

    ``now`` must be **the moment of capture**, not the moment of writing. A
    lidar observation is usable for 0.3 s, so evaluating it against the clock
    after the report has been written makes every scan stale and records
    nothing — silently, because a refusal to remember is not an error. The
    question a memory asks is "was this trustworthy when it was taken", and the
    claim then carries its own observation time and expiry for later readers.
    """
    if not observation_documents:
        return RecordingResult(0)
    observation = load_observation(observation_documents[-1])
    return recorder.record_obstacle_scan(observation, now, pose=pose, grid=grid, artifact=artifact)


# The gz `default` world places home here, and the X500 spawns at that point
# facing world +X, which PX4 maps to north. The obstacle is placed at +X, so a
# zero-yaw pose at the grid origin is the vehicle's actual state during this
# scenario — not an assumed one.
GZ_DEFAULT_HOME_LATITUDE_DEG = 47.397971
GZ_DEFAULT_HOME_LONGITUDE_DEG = 8.546164
SCENARIO_POSE = VehiclePose(GZ_DEFAULT_HOME_LATITUDE_DEG, GZ_DEFAULT_HOME_LONGITUDE_DEG, yaw_deg=0.0)
SCENARIO_GRID = MapGrid(GZ_DEFAULT_HOME_LATITUDE_DEG, GZ_DEFAULT_HOME_LONGITUDE_DEG, cell_size_m=2.0)


# The scenario pins its own world instead of accepting the launcher's default.
# Two reasons, and both are the difference between a measurement and a guess:
# the gz topic and service names *contain* the world name, so a mismatch means
# the run finds nothing; and only an empty world lets "the lidar saw the placed
# obstacle" mean anything — in a populated world the sensor also sees terrain,
# buildings and props, which the scoring would happily count as the obstacle.
SCENARIO_WORLD = "default"


def scan_topic(world: str = SCENARIO_WORLD) -> str:
    """The gz topic carrying the lidar scan in a given world."""
    return f"/world/{world}/model/x500_lidar_2d_0/link/link/sensor/lidar_2d_v2/scan"


def create_service(world: str = SCENARIO_WORLD) -> str:
    """The gz service that spawns an entity into a given world."""
    return f"/world/{world}/create"
_LAUNCHER = Path("simulation/gazebo/launch/run_px4_gazebo_headless.zsh")
_OBSTACLE_SDF = (
    '<sdf version="1.9"><model name="obstacle_front"><static>true</static>'
    '<pose>5 0 1 0 0 0</pose><link name="l">'
    '<collision name="c"><geometry><box><size>1 1 2</size></box></geometry></collision>'
    '<visual name="v"><geometry><box><size>1 1 2</size></box></geometry></visual>'
    "</link></model></sdf>"
)
# The box sits 5 m ahead; its near face is ~4.4 m from the lidar. It is placed at
# world +X, and the drone spawns facing +X, so it must appear straight ahead.
FRONT_OBSTACLE = ExpectedObstacle(sector_yaw_deg=0.0, distance_m=4.4, distance_tolerance_m=0.6)


def run_obstacle_scenario(
    output_directory: Path,
    *,
    scans: int = 30,
    startup_wait_s: float = 32.0,
    project_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    world_recorder: WorldMemoryRecorder | None = None,
) -> Path:
    """Drive a real lidar SITL past a known obstacle and record what it saw."""
    root = project_root or Path(__file__).resolve().parents[2]
    clock = now or (lambda: datetime.now(UTC))
    environment = {**_gz_environment()}
    launcher = subprocess.Popen(
        (str(_LAUNCHER), "lidar-2d"),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        sleep(startup_wait_s)
        _spawn_obstacle(environment)
        sleep(2.0)
        messages = _capture_scans(scans, environment)
        observations = observations_from_scans(
            messages, vehicle_id="x500v2_reference_01", sensor_id="lidar_2d", now=clock
        )
        captured_at = clock()
        report = evaluate_obstacle_scenario(observations, FRONT_OBSTACLE)
    finally:
        launcher.terminate()
        try:
            launcher.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            launcher.kill()
    report_path = write_report(report, output_directory, now=clock)
    if world_recorder is not None:
        # The artifact is the evidence of record; world memory is the derived,
        # perishable summary, so it is written after the report is safe on disk
        # — but judged against the capture clock, or every scan is already stale.
        recording = remember_scanned_world(
            observations,
            world_recorder,
            pose=SCENARIO_POSE,
            grid=SCENARIO_GRID,
            now=captured_at,
            artifact=str(report_path),
        )
        # Never swallow this: a run that remembered nothing must say so, or the
        # empty world log reads as "the sensor saw nothing worth keeping".
        print(
            f"World memory: {recording.written} claim(s) recorded"
            + (f", {recording.dropped} dropped" if recording.dropped else "")
            + (f" — {recording.failure}" if recording.failure else "")
        )
    return report_path


def _gz_environment() -> dict[str, str]:
    import os

    # The launcher pins the Gazebo server to this interface; a client that does
    # not match it discovers nothing.  PX4_GZ_WORLD is set here rather than left
    # to the launcher default so that the world the launcher starts and the
    # world in every topic and service name below are the same string.
    return {**os.environ, "GZ_IP": "127.0.0.1", "PX4_GZ_WORLD": SCENARIO_WORLD}


def _spawn_obstacle(environment: dict[str, str]) -> None:
    result = subprocess.run(
        (
            "gz", "service", "-s", create_service(),
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", f'name: "obstacle_front", allow_renaming: false, sdf: {json.dumps(_OBSTACLE_SDF)}',
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )
    if "true" not in result.stdout.lower():
        raise LidarObstacleError(f"Could not spawn the obstacle: {result.stdout.strip() or result.stderr.strip()}")


def _capture_scans(scans: int, environment: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        ("gz", "topic", "-e", "-t", scan_topic(), "--json-output", "-n", str(scans)),
        capture_output=True, text=True, timeout=60.0, check=False, env=environment,
    )
    messages = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def write_report(report: ObstacleScenarioReport, output_directory: Path, *, now: Callable[[], datetime]) -> Path:
    """Persist one obstacle-scenario report as a durable artifact."""
    timestamp = now().astimezone(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"obstacle-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(
        json.dumps(
            {
                "started_at": timestamp.isoformat().replace("+00:00", "Z"),
                "verification_level": "px4-gazebo-fault-injection",
                "sensor": "gz_x500_lidar_2d",
                **asdict(report),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def main(arguments: tuple[str, ...] | None = None) -> int:
    """Run the headless obstacle scenario and print the artifact path."""
    parser = argparse.ArgumentParser(description="Drive a lidar SITL past a known obstacle and record the result.")
    parser.add_argument("--scans", type=int, default=30, help="How many scans to capture and score.")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("simulation/artifacts/perception"),
        help="Where to write the obstacle-scenario artifact.",
    )
    parser.add_argument(
        "--world-memory-file", type=Path, default=DEFAULT_WORLD_MEMORY_PATH,
        help="Where the freshest scan is remembered as perishable world evidence.",
    )
    parser.add_argument(
        "--no-world-memory", action="store_true",
        help="Score the run without remembering it; the scenario artifact is unaffected.",
    )
    parsed = parser.parse_args(arguments)
    report_path = run_obstacle_scenario(
        parsed.output_dir,
        scans=parsed.scans,
        world_recorder=None if parsed.no_world_memory else WorldMemoryRecorder(parsed.world_memory_file),
    )
    document = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"Obstacle scenario: {document['verdict']} — {report_path}")
    print(f"  {document['detail']}")
    return 0 if document["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
