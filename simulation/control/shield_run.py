"""Fly the shield against a real PX4, Gazebo and lidar. Isolated from scoring.

Four runs, each isolating one thing the shield claims:

* ``clear-path``     nothing in the way. Measures the false-stop rate, which is
                     the number that decides whether a shield is usable at all.
* ``static-obstacle`` a box on the flight path. Measures whether the vehicle
                     stopped, and whether it stopped *in time*.
* ``blind-sector``   a command backwards, into the 90 degrees a 270-degree
                     lidar cannot see. Nothing is there; the shield must refuse
                     anyway, because unobserved is not clear.
* ``stale-sensor``   scans stop arriving mid-flight. The obstacle map is still
                     in memory and still says the path is clear; the shield must
                     stop trusting it.

The control loop is the real one: nominal velocity, then the shield, then the
Offboard boundary, then the adapter. Nothing is mocked between the planner and
PX4 -- that is the point of doing this in SITL rather than in a unit test.

Clearance comes from Gazebo ground truth, not from the estimator the shield was
reading. Asking the same source twice would answer a different question.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from brain.adapters.offboard_fallback import OffboardFallbackExecutor
from brain.adapters.offboard_velocity_adapter import MavsdkVelocityAdapter
from brain.control.contract import Velocity, load_setpoint
from brain.control.session import OffboardSession
from brain.control.shield import RuntimeSafetyShield, ShieldMode
from brain.perception.lidar_obstacle import laser_scan_from_gz_json, obstacle_observation
from brain.safety.profile import load_safety_profile
from brain.telemetry.observation import load_observation
from simulation.control.shield_scenario import (
    ShieldSample,
    ShieldScenarioReport,
    clearance_to,
    evaluate_shield_run,
)
from simulation.perception.obstacle_scenario import SCENARIO_WORLD, create_service, scan_topic


_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _ROOT / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh"
_ENDPOINT = "udpin://0.0.0.0:14540"
_MODEL_NAME = "x500_lidar_2d_0"
_POSE_TOPIC = f"/world/{SCENARIO_WORLD}/dynamic_pose/info"
_SENSOR_ID = "lidar_2d_v2"

_STREAM_HZ = 5.0
_NOMINAL_SPEED_M_S = 0.6
#: Where the box goes, in Gazebo's world frame. Far enough that the run has a
#: clear stretch first, so a stop can be attributed to the obstacle.
_OBSTACLE_XY = (12.0, 0.0)

SCENARIOS = ("clear-path", "static-obstacle", "blind-sector", "stale-sensor")


def run_shield_scenario(
    scenario: str,
    artifact_directory: Path,
    *,
    mode: str = "shadow",
    altitude_m: float = 2.0,
    fly_seconds: float = 25.0,
    startup_wait_s: float = 32.0,
) -> ShieldScenarioReport:
    """Start SITL, fly one shield scenario, and write the evidence artifact."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'; expected one of {SCENARIOS}.")
    artifact_directory.mkdir(parents=True, exist_ok=True)
    # PX4_GZ_WORLD is set here rather than left to the launcher's default. The
    # lidar-2d profile otherwise spawns into `baylands`, whose terrain and
    # buildings the lidar also sees -- so "the shield stopped for the box we
    # placed" would be unprovable. It is also the world every topic and service
    # name below is built from, and a mismatch finds nothing at all.
    environment = {**os.environ, "GZ_IP": "127.0.0.1", "PX4_GZ_WORLD": SCENARIO_WORLD}

    launcher = subprocess.Popen(
        (str(_LAUNCHER), "lidar-2d"), cwd=_ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment,
    )
    scan_capture: subprocess.Popen | None = None
    pose_capture: subprocess.Popen | None = None
    scan_path = artifact_directory / f".{scenario}-scans.jsonl"
    pose_path = artifact_directory / f".{scenario}-pose.jsonl"
    try:
        time.sleep(startup_wait_s)
        if scenario == "static-obstacle":
            _spawn_obstacle(environment)
            time.sleep(2.0)
        scan_capture = _stream_topic(scan_topic(), scan_path, environment)
        pose_capture = _stream_topic(_POSE_TOPIC, pose_path, environment)
        time.sleep(2.0)
        report = asyncio.run(
            _fly(
                scenario=scenario, mode=mode, altitude_m=altitude_m,
                fly_seconds=fly_seconds, scan_path=scan_path, pose_path=pose_path,
                scan_capture=scan_capture,
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

    destination = artifact_directory / f"shield-{scenario}-{_stamp()}.json"
    destination.write_text(json.dumps(report.as_document(), indent=2) + "\n", encoding="utf-8")
    print(f"evidence: {destination}")
    return report


async def _fly(
    *, scenario: str, mode: str, altitude_m: float, fly_seconds: float,
    scan_path: Path, pose_path: Path, scan_capture: subprocess.Popen,
) -> ShieldScenarioReport:
    from mavsdk import System

    profile = load_safety_profile()
    assert profile.shield is not None and profile.offboard is not None

    shield_mode = ShieldMode.ACTIVE if mode == "active" else ShieldMode.SHADOW
    shield = RuntimeSafetyShield(_enabled(profile.shield), mode=shield_mode)
    scenario_profile = _profile_with_offboard(profile, _enabled(profile.offboard))

    drone = System()
    await drone.connect(system_address=_ENDPOINT)
    await _await_connection(drone)

    adapter = MavsdkVelocityAdapter(drone)
    session = OffboardSession(scenario_profile, adapter, shadow=False)
    samples: list[ShieldSample] = []
    started_at = time.monotonic()

    try:
        await drone.action.arm()
        await drone.action.set_takeoff_altitude(altitude_m)
        await drone.action.takeoff()
        await asyncio.sleep(9.0)

        # Backwards for the blind-sector run: the lidar sees -135..+135, so
        # nothing reports on 180 degrees and the shield must refuse on coverage
        # alone, with no obstacle involved.
        nominal = Velocity(
            -_NOMINAL_SPEED_M_S if scenario == "blind-sector" else _NOMINAL_SPEED_M_S,
            0.0, 0.0, 0.0,
        )
        stream_id = f"shield-{uuid.uuid4().hex[:8]}"
        sequence = 0
        entered = False
        stale_cut_at = started_at + 9.0 + fly_seconds / 2.0
        deadline = time.monotonic() + fly_seconds

        while time.monotonic() < deadline:
            now = datetime.now(UTC)
            if scenario == "stale-sensor" and time.monotonic() > stale_cut_at:
                # The sensor stops. The last map is still in memory and still
                # says the path ahead is clear; the shield must stop trusting it
                # rather than keep flying on remembered evidence.
                if scan_capture.poll() is None:
                    scan_capture.terminate()

            observation = _latest_observation(scan_path, vehicle_id=profile.vehicle_id)
            decision = shield.evaluate(nominal, observation, now)
            commanded = decision.velocity

            setpoint = load_setpoint(_setpoint_document(
                profile.vehicle_id, stream_id, sequence, now, commanded
            ))
            await session.deliver(session.offer(setpoint, now), now)
            sequence += 1
            if not entered:
                await adapter.start()
                entered = True

            samples.append(ShieldSample(
                at_s=round(time.monotonic() - started_at, 3),
                nominal_speed_m_s=round(_speed(nominal), 3),
                verdict=decision.verdict.value,
                commanded_speed_m_s=round(_speed(commanded), 3),
                clearance_m=_ground_truth_clearance(pose_path) if scenario == "static-obstacle" else None,
            ))
            await asyncio.sleep(1.0 / _STREAM_HZ)

        await OffboardFallbackExecutor(drone, adapter).execute(("zero_velocity", "hold", "land"))
        await asyncio.sleep(8.0)
    finally:
        await adapter.stop()

    return evaluate_shield_run(
        scenario=scenario,
        mode=mode,
        samples=samples,
        required_clearance_m=profile.shield.minimum_clearance_m,
        expect_intervention=scenario != "clear-path",
        requires_sensor=scenario in ("clear-path", "static-obstacle"),
        requires_ground_truth=scenario == "static-obstacle",
        recorded_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _setpoint_document(vehicle_id, stream_id, sequence, now, velocity) -> dict:
    return {
        "contract_version": "v0.1",
        "vehicle_id": vehicle_id,
        "stream_id": stream_id,
        "sequence": sequence,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "ttl_s": 0.4,
        "frame": "body_frd",
        "kind": "velocity",
        "validity": "valid",
        "velocity": {
            "x_m_s": velocity.x_m_s, "y_m_s": velocity.y_m_s,
            "z_m_s": velocity.z_m_s, "yaw_rate_deg_s": velocity.yaw_rate_deg_s,
        },
    }


def _latest_observation(scan_path: Path, *, vehicle_id: str):
    """Build an obstacle observation from the newest captured scan, or none.

    ``observed_at`` is the capture file's modification time, not ``now()``.
    Stamping every reading with the current clock is how the first run of the
    stale-sensor scenario failed to go stale at all: the scans had stopped
    arriving and the harness kept re-dating the last one, laundering a frozen
    obstacle map into fresh evidence. When the capture dies the mtime stops
    advancing and the observation ages out on its own, which is the behaviour
    the scenario is supposed to be testing.
    """
    try:
        observed_at = datetime.fromtimestamp(scan_path.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    message = _last_json_object(scan_path)
    if message is None:
        return None
    try:
        document = obstacle_observation(
            laser_scan_from_gz_json(message),
            vehicle_id=vehicle_id,
            observed_at=observed_at,
            sensor_id=_SENSOR_ID,
        )
        return load_observation(document)
    except Exception:  # noqa: BLE001 - an unreadable scan is simply no evidence
        return None


def _ground_truth_clearance(pose_path: Path) -> float | None:
    """The vehicle's distance to the obstacle, from Gazebo rather than the estimator."""
    message = _last_json_object(pose_path)
    if message is None:
        return None
    for pose in message.get("pose", []) if isinstance(message, dict) else []:
        if isinstance(pose, dict) and pose.get("name") == _MODEL_NAME:
            position = pose.get("position", {})
            return round(
                clearance_to(
                    (float(position.get("x", 0.0)), float(position.get("y", 0.0))), _OBSTACLE_XY
                ),
                3,
            )
    return None


def _last_json_object(path: Path) -> dict | None:
    """The most recent complete JSON object in a streamed capture.

    gz writes pretty-printed JSON, so the file is a stream of multi-line
    objects rather than one per line. Scanning back for the last balanced
    object is what makes a partially written tail harmless.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    depth = 0
    end = None
    for index in range(len(text) - 1, -1, -1):
        character = text[index]
        if character == "}":
            if end is None:
                end = index
            depth += 1
        elif character == "{":
            depth -= 1
            if depth == 0 and end is not None:
                try:
                    return json.loads(text[index : end + 1])
                except json.JSONDecodeError:
                    end = None
                    depth = 0
    return None


def _stream_topic(topic: str, destination: Path, environment: dict) -> subprocess.Popen:
    stream = destination.open("w", encoding="utf-8")
    return subprocess.Popen(
        ("gz", "topic", "-e", "-t", topic, "--json-output"),
        stdout=stream, stderr=subprocess.DEVNULL, env=environment,
    )


def _spawn_obstacle(environment: dict) -> None:
    sdf = (
        f'<sdf version="1.9"><model name="shield_obstacle"><static>true</static>'
        f'<pose>{_OBSTACLE_XY[0]} {_OBSTACLE_XY[1]} 1.5 0 0 0</pose><link name="l">'
        '<collision name="c"><geometry><box><size>2 4 3</size></box></geometry></collision>'
        '<visual name="v"><geometry><box><size>2 4 3</size></box></geometry></visual>'
        "</link></model></sdf>"
    )
    result = subprocess.run(
        (
            "gz", "service", "-s", create_service(),
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", f'name: "shield_obstacle", allow_renaming: false, sdf: {json.dumps(sdf)}',
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )
    if "true" not in result.stdout.lower():
        raise RuntimeError(f"Could not spawn the obstacle: {result.stdout.strip() or result.stderr.strip()}")


async def _await_connection(drone) -> None:
    async for state in drone.core.connection_state():
        if state.is_connected:
            return


def _speed(velocity: Velocity) -> float:
    from math import hypot

    return hypot(velocity.x_m_s, velocity.y_m_s)


def _enabled(limits):
    from dataclasses import replace

    return replace(limits, enabled=True)


def _profile_with_offboard(profile, offboard):
    from dataclasses import replace

    return replace(profile, offboard=offboard)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
