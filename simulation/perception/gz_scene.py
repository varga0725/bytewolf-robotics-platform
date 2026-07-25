"""Read the Gazebo scene the down-camera scenarios share, and spawn a marker in it.

Both the ground-truth check and the autonomous approach need the same three
things from a running simulation: the vehicle's pose, a single down-camera frame,
and a marker placed at a known world spot. Keeping them here gives those
scenarios one source of the frame convention and the pose maths, the convention
that a down-camera SITL run confirmed against ground truth.

Everything here touches gz transport only -- it reads topics and calls the world
factory service. It never opens a PX4 or MAVSDK connection and commands no
motion, so it stays on the read-only side of the safety architecture; the marker
it spawns is a scene object, not a vehicle command.
"""

from __future__ import annotations

import base64
from datetime import datetime
import json
from math import atan2, degrees, sqrt
import subprocess

from brain.perception.camera_frame import CameraFrame, FrameEncoding


DEFAULT_WORLD = "default"
POSE_TOPIC = "/world/default/pose/info"
# The mono_cam horizontal FOV is fixed regardless of resolution; a scenario builds
# its intrinsics from the captured frame's actual size, so it is correct at PX4's
# stock 1280x960 and at the 1080p overlay alike.
MONO_CAM_HORIZONTAL_FOV_RAD = 1.74


def image_topic(model_name: str) -> str:
    """The down-camera image topic for a spawned airframe model."""
    return f"/world/{DEFAULT_WORLD}/model/{model_name}/link/camera_link/sensor/imager/image"


def one_message(topic: str, environment: dict[str, str], *, timeout: float) -> dict | None:
    """Read exactly one JSON message off a gz topic, or None if none arrives."""
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


def drone_pose(environment: dict[str, str], model_name: str) -> dict | None:
    """The vehicle's world position and its yaw/tilt in degrees, or None."""
    message = one_message(POSE_TOPIC, environment, timeout=10.0)
    for pose in (message or {}).get("pose", []):
        if pose.get("name") == model_name:
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


def model_xy(environment: dict[str, str], name: str) -> tuple[float, float] | None:
    """The world (x, y) of a named model, or None if it is not in the pose set."""
    message = one_message(POSE_TOPIC, environment, timeout=10.0)
    for pose in (message or {}).get("pose", []):
        if pose.get("name") == name:
            position = pose.get("position", {})
            return float(position.get("x", 0.0)), float(position.get("y", 0.0))
    return None


def capture_frame(
    captured_at: datetime,
    environment: dict[str, str],
    *,
    topic: str,
    sensor_id: str = "down_rgb",
    frame_id: str = "down-capture",
) -> CameraFrame | None:
    """Read one RGB8 down-camera frame off its topic, or None on a bad read."""
    message = one_message(topic, environment, timeout=15.0)
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
        sensor_id=sensor_id, encoding=FrameEncoding.RGB8, width=width, height=height,
        data=data, captured_at=captured_at, frame_id=frame_id,
    )


def spawn_marker(
    environment: dict[str, str],
    *,
    xy: tuple[float, float],
    name: str = "target_marker",
) -> None:
    """Place a static red 1 m box at a known world spot for a scenario to find."""
    x, y = xy
    sdf = (
        f'<sdf version="1.9"><model name="{name}"><static>true</static>'
        f'<pose>{x} {y} 0.02 0 0 0</pose><link name="l">'
        '<visual name="v"><geometry><box><size>1 1 0.04</size></box></geometry>'
        '<material><ambient>0.86 0.08 0.08 1</ambient><diffuse>0.86 0.08 0.08 1</diffuse>'
        '<emissive>0.5 0 0 1</emissive></material></visual>'
        '<collision name="c"><geometry><box><size>1 1 0.04</size></box></geometry></collision>'
        "</link></model></sdf>"
    )
    subprocess.run(
        (
            "gz", "service", "-s", f"/world/{DEFAULT_WORLD}/create",
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", f'name: "{name}", allow_renaming: false, sdf: {json.dumps(sdf)}',
        ),
        capture_output=True, text=True, timeout=15.0, check=False, env=environment,
    )
