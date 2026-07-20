"""Record what a survey flight sees, without touching the flight.

A survey that flies the pattern and remembers nothing is a battery-powered
tour. This observer closes that: it reads the lidar topic and the dashboard's
telemetry snapshot — both read-only — pairs each scan with where the vehicle
was and which way it faced, and records the result as world-memory claims.

It is a separate process on purpose. The mission adapter talks to MAVSDK and
nothing else, so mapping cannot slow, block, or fail a flight: the worst a
broken observer can do is remember less.

Pairing is where the honesty lives. A scan is only placed on the map when a
fresh pose exists for it. A missing heading is not north, a stale position is
not the current one, and either of them turns a wall into a wall somewhere
nobody measured — so the scan is dropped instead, and the drop is counted.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import cos, radians
from pathlib import Path
import subprocess
import time

from apps.dashboard.telemetry import TelemetryFormatError, TelemetrySnapshot, load_telemetry_snapshot
from brain.memory.recorder import DEFAULT_WORLD_MEMORY_PATH, RecordingResult, WorldMemoryRecorder
from brain.memory.world_map import MapGrid, VehiclePose
from brain.perception.lidar_obstacle import laser_scan_from_gz_json, obstacle_observation
from brain.telemetry.observation import load_observation


_EARTH_RADIUS_M = 6_371_000.0
# A pose older than this belonged to a different part of the sweep. At 3 m/s a
# second is three metres of error, which is more than one map cell.
MAX_POSE_AGE_S = 1.5
DEFAULT_SCAN_INTERVAL_S = 2.0


class SurveyRecorderError(RuntimeError):
    """The observer cannot pair scans with poses well enough to map anything."""


@dataclass(frozen=True)
class SurveyProgress:
    """What one observation run managed to place on the map."""

    scans_seen: int = 0
    scans_mapped: int = 0
    claims_written: int = 0
    dropped_no_pose: int = 0

    def with_scan(self, *, mapped: bool, claims: int = 0, no_pose: bool = False) -> SurveyProgress:
        return SurveyProgress(
            scans_seen=self.scans_seen + 1,
            scans_mapped=self.scans_mapped + (1 if mapped else 0),
            claims_written=self.claims_written + claims,
            dropped_no_pose=self.dropped_no_pose + (1 if no_pose else 0),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "scans_seen": self.scans_seen,
            "scans_mapped": self.scans_mapped,
            "claims_written": self.claims_written,
            "dropped_no_pose": self.dropped_no_pose,
        }


def pose_from_snapshot(
    snapshot: TelemetrySnapshot, grid: MapGrid, now: datetime, *, max_age_s: float = MAX_POSE_AGE_S
) -> VehiclePose | None:
    """Place the vehicle on the grid, or refuse to place it at all.

    Returns ``None`` — never a default — when the position is missing, the
    heading is unknown, or the snapshot is older than one sweep leg. Each of
    those would silently move every obstacle in the scan.
    """
    if snapshot.position is None or snapshot.heading_deg is None or snapshot.captured_at is None:
        return None
    try:
        captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if captured.tzinfo is None:
        return None
    age_s = (now.astimezone(UTC) - captured.astimezone(UTC)).total_seconds()
    if not -max_age_s <= age_s <= max_age_s:
        return None
    north_m = radians(snapshot.position.latitude_deg - grid.origin_latitude_deg) * _EARTH_RADIUS_M
    east_m = (
        radians(snapshot.position.longitude_deg - grid.origin_longitude_deg)
        * _EARTH_RADIUS_M
        * cos(radians(grid.origin_latitude_deg))
    )
    return VehiclePose(
        latitude_deg=snapshot.position.latitude_deg,
        longitude_deg=snapshot.position.longitude_deg,
        yaw_deg=snapshot.heading_deg,
        north_m=north_m,
        east_m=east_m,
    )


def record_survey_scan(
    scan_message: dict,
    snapshot: TelemetrySnapshot,
    recorder: WorldMemoryRecorder,
    grid: MapGrid,
    now: datetime,
    *,
    vehicle_id: str = "x500v2_reference_01",
    sensor_id: str = "lidar_2d",
    progress: SurveyProgress | None = None,
) -> SurveyProgress:
    """Pair one scan with the live pose and record it, or count the drop."""
    state = progress or SurveyProgress()
    pose = pose_from_snapshot(snapshot, grid, now)
    if pose is None:
        return state.with_scan(mapped=False, no_pose=True)
    document = obstacle_observation(
        laser_scan_from_gz_json(scan_message), vehicle_id=vehicle_id, observed_at=now, sensor_id=sensor_id
    )
    observation = load_observation(document)
    result: RecordingResult = recorder.record_obstacle_scan(observation, now, pose=pose, grid=grid)
    return state.with_scan(mapped=result.written > 0, claims=result.written)


def run_survey_observer(
    *,
    telemetry_path: Path,
    world_memory_path: Path,
    grid: MapGrid,
    scan_topic: str,
    duration_s: float,
    interval_s: float = DEFAULT_SCAN_INTERVAL_S,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SurveyProgress:
    """Observe a running flight for a bounded time and map what it sees.

    The observer never starts, stops, or waits for a mission: it samples for as
    long as it was asked to. A flight that ends early simply leaves the rest of
    the samples unpaired, which is visible in the progress counters rather than
    hidden.
    """
    clock = now or (lambda: datetime.now(UTC))
    recorder = WorldMemoryRecorder(world_memory_path)
    deadline = clock().timestamp() + duration_s
    progress = SurveyProgress()
    while clock().timestamp() < deadline:
        message = _capture_one_scan(scan_topic)
        try:
            snapshot = load_telemetry_snapshot(telemetry_path)
        except TelemetryFormatError:
            snapshot = TelemetrySnapshot(None, None, None, None, None)
        if message is not None:
            progress = record_survey_scan(message, snapshot, recorder, grid, clock())
        sleep(interval_s)
    return progress


def _capture_one_scan(scan_topic: str) -> dict | None:
    import os

    result = subprocess.run(
        ("gz", "topic", "-e", "-t", scan_topic, "--json-output", "-n", "1"),
        capture_output=True, text=True, timeout=20.0, check=False,
        env={**os.environ, "GZ_IP": "127.0.0.1"},
    )
    for line in result.stdout.splitlines():
        if line.strip():
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def main(arguments: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map what a running survey flight sees. Reads only; never commands the drone."
    )
    parser.add_argument("--duration", type=float, default=120.0, help="How long to observe, in seconds.")
    parser.add_argument("--interval", type=float, default=DEFAULT_SCAN_INTERVAL_S)
    parser.add_argument(
        "--telemetry-file", type=Path, default=Path("simulation/artifacts/dashboard/live-telemetry.json")
    )
    parser.add_argument("--world-memory-file", type=Path, default=DEFAULT_WORLD_MEMORY_PATH)
    parser.add_argument("--home-latitude", type=float, default=47.397971)
    parser.add_argument("--home-longitude", type=float, default=8.546164)
    parser.add_argument("--cell-size", type=float, default=2.0)
    parser.add_argument(
        "--scan-topic",
        default="/world/default/model/x500_lidar_2d_0/link/link/sensor/lidar_2d_v2/scan",
    )
    parsed = parser.parse_args(arguments)
    progress = run_survey_observer(
        telemetry_path=parsed.telemetry_file,
        world_memory_path=parsed.world_memory_file,
        grid=MapGrid(parsed.home_latitude, parsed.home_longitude, cell_size_m=parsed.cell_size),
        scan_topic=parsed.scan_topic,
        duration_s=parsed.duration,
        interval_s=parsed.interval,
    )
    print(
        f"Survey observer: {progress.scans_mapped}/{progress.scans_seen} scans mapped, "
        f"{progress.claims_written} claims, {progress.dropped_no_pose} dropped without a usable pose."
    )
    return 0 if progress.claims_written else 1


if __name__ == "__main__":
    raise SystemExit(main())
