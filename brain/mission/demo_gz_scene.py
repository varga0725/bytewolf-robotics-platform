"""Gazebo GUI helpers for demo-waypoint visual evidence (read-only transport, no flight commands)."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from brain.mission.commands import WaypointCommand
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig

# Baylands px4 gz_x500 spawn in world ENU (meters). north_m/east_m are NED offsets from home.
# Documented in docs/ros2-humble-bridge-ubuntu-runbook.md.
BAYLANDS_DEMO_SPAWN_WORLD_X_M = 205.0
BAYLANDS_DEMO_SPAWN_WORLD_Y_M = 155.0
BAYLANDS_DEMO_GROUND_Z_OFFSET_M = 0.4
DEFAULT_GZ_WORLD_NAME = "baylands"
DEFAULT_FOLLOW_MODEL_NAME = "x500_0"
_FOLLOW_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_FOLLOW_OFFSET = (-5.0, -1.5, 2.5)
_FOLLOW_OFFSET_LIMITS = {
    "x": (-25.0, 5.0),
    "y": (-15.0, 15.0),
    "z": (0.5, 25.0),
}
_DEMO_CAMERA_MODES = frozenset({"overview", "follow", "off"})
DEFAULT_DEMO_GZ_CAMERA_MODE = "overview"
_OVERVIEW_HFOV_DEG = 60.0
_OVERVIEW_ASPECT_W_H = 16.0 / 9.0
_OVERVIEW_BOUND_MARGIN = 0.15
_OVERVIEW_AZIMUTH_RAD = math.radians(225.0)
_OVERVIEW_ELEVATION_RAD = math.radians(35.0)
_OVERVIEW_POSE_LIMITS = {
    "x": (0.0, 2000.0),
    "y": (0.0, 2000.0),
    "z": (0.5, 500.0),
    "roll": (-math.pi, math.pi),
    "pitch": (-math.pi / 2, math.pi / 2),
    "yaw": (-math.pi, math.pi),
}
_MOVE_TO_POSE_SERVICE = "/gui/move_to/pose"
_DEFAULT_MOVE_TO_REQTYPE = "gz.msgs.GUICamera"
_DEFAULT_MOVE_TO_REPTYPE = "gz.msgs.Boolean"
_DEMO_GUI_CONFIG_REL = Path("config/gz/demo_gui.config")
_MARKER_NAMESPACE = "demo_waypoint"
_WAYPOINT_MARKER_COLORS: tuple[tuple[float, float, float], ...] = (
    (0.92, 0.15, 0.15),
    (0.15, 0.55, 0.92),
    (0.15, 0.82, 0.35),
    (0.95, 0.65, 0.10),
    (0.70, 0.20, 0.85),
    (0.20, 0.85, 0.85),
    (0.95, 0.35, 0.55),
    (0.55, 0.55, 0.15),
    (0.35, 0.35, 0.90),
    (0.85, 0.45, 0.25),
    (0.40, 0.90, 0.55),
    (0.90, 0.90, 0.20),
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_demo_gui_config_path() -> Path:
    return (repository_root() / _DEMO_GUI_CONFIG_REL).resolve()


def demo_gui_config_enabled() -> bool:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_GUI_CONFIG", "1").strip().lower()
    return raw not in {"0", "false", "no"}


@dataclass(frozen=True)
class OverviewPose6:
    """GUI camera pose as position plus roll/pitch/yaw (radians), matching demo_gui.config."""

    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float

    def as_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "roll": self.roll,
            "pitch": self.pitch,
            "yaw": self.yaw,
        }

    def as_pose_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.roll, self.pitch, self.yaw]


def parse_demo_gz_camera_mode_env() -> tuple[str, str | None]:
    """Return requested camera mode and validation error (invalid values still default to overview at runtime)."""
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_CAMERA_MODE", DEFAULT_DEMO_GZ_CAMERA_MODE).strip().lower()
    if raw in _DEMO_CAMERA_MODES:
        return raw, None
    return DEFAULT_DEMO_GZ_CAMERA_MODE, f"BYTEWOLF_DEMO_GZ_CAMERA_MODE must be one of {sorted(_DEMO_CAMERA_MODES)}"


def effective_demo_gz_camera_mode() -> tuple[str, str | None]:
    """Effective mode after validation (unknown env values fall back to overview with an error)."""
    return parse_demo_gz_camera_mode_env()


def parse_overview_pose_env() -> tuple[OverviewPose6 | None, str | None]:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_OVERVIEW_POSE", "").strip()
    if not raw:
        return None, None
    parts = raw.replace(",", " ").split()
    if len(parts) != 6:
        return None, "BYTEWOLF_DEMO_GZ_OVERVIEW_POSE must have six numbers (x y z roll pitch yaw)"
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None, "BYTEWOLF_DEMO_GZ_OVERVIEW_POSE must be numeric"
    fields = ("x", "y", "z", "roll", "pitch", "yaw")
    for name, value in zip(fields, values):
        low, high = _OVERVIEW_POSE_LIMITS[name]
        if not (low <= value <= high) or not math.isfinite(value):
            return None, f"BYTEWOLF_DEMO_GZ_OVERVIEW_POSE {name}={value} outside [{low}, {high}]"
    return OverviewPose6(*values), None  # type: ignore[arg-type]


def parse_gui_config_overview_pose(config_path: Path | None = None) -> OverviewPose6 | None:
    path = config_path or default_demo_gui_config_path()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("<camera_pose>"):
            continue
        inner = stripped.replace("<camera_pose>", "").replace("</camera_pose>", "").strip()
        parts = inner.split()
        if len(parts) != 6:
            return None
        try:
            values = [float(part) for part in parts]
        except ValueError:
            return None
        return OverviewPose6(*values)  # type: ignore[arg-type]
    return None


def mission_overview_world_points(
    config: DemoWaypointMissionConfig,
    *,
    spawn_world_x_m: float = BAYLANDS_DEMO_SPAWN_WORLD_X_M,
    spawn_world_y_m: float = BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
    ground_z_offset_m: float = BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
) -> list[tuple[float, float, float]]:
    """World points that must stay inside the overview frustum (home, climb, waypoints, landing)."""
    points: list[tuple[float, float, float]] = [
        (spawn_world_x_m, spawn_world_y_m, ground_z_offset_m),
        (spawn_world_x_m, spawn_world_y_m, ground_z_offset_m + config.takeoff_altitude_m),
    ]
    for waypoint in config.waypoints:
        points.append(
            waypoint_world_position(
                waypoint,
                spawn_world_x_m=spawn_world_x_m,
                spawn_world_y_m=spawn_world_y_m,
                ground_z_offset_m=ground_z_offset_m,
            )
        )
    return points


def _overview_vertical_fov_rad() -> float:
    hfov = math.radians(_OVERVIEW_HFOV_DEG)
    return 2.0 * math.atan(math.tan(hfov / 2.0) / _OVERVIEW_ASPECT_W_H)


def _euler_to_quaternion(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy
    return qx, qy, qz, qw


def _quaternion_to_euler(qx: float, qy: float, qz: float, qw: float) -> tuple[float, float, float]:
    sinr_cosp = 2.0 * (qw * qx + qy * qz)
    cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2.0 * (qw * qy - qz * qx)
    pitch = math.asin(max(-1.0, min(1.0, sinp)))
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


def _normalize(vec: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2)
    if length <= 1e-9:
        return (0.0, 0.0, 1.0)
    return (vec[0] / length, vec[1] / length, vec[2] / length)


def quaternion_look_at(eye: tuple[float, float, float], target: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """World-frame quaternion so the camera at ``eye`` looks at ``target`` (camera forward = -Z in OGRE)."""
    forward = _normalize((target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]))
    world_up = (0.0, 0.0, 1.0)
    right = _normalize(
        (
            forward[1] * world_up[2] - forward[2] * world_up[1],
            forward[2] * world_up[0] - forward[0] * world_up[2],
            forward[0] * world_up[1] - forward[1] * world_up[0],
        )
    )
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    # Rotation matrix [right, up, -forward] as column basis for camera-to-world.
    m00, m01, m02 = right[0], up[0], -forward[0]
    m10, m11, m12 = right[1], up[1], -forward[1]
    m20, m21, m22 = right[2], up[2], -forward[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        qw = (m21 - m12) / s
        qx = 0.25 * s
        qy = (m01 + m10) / s
        qz = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        qw = (m02 - m20) / s
        qx = (m01 + m10) / s
        qy = 0.25 * s
        qz = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        qw = (m10 - m01) / s
        qx = (m02 + m20) / s
        qy = (m12 + m21) / s
        qz = 0.25 * s
    length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    return qx / length, qy / length, qz / length, qw / length


def _rotate_vector_by_quaternion(
    vec: tuple[float, float, float], qx: float, qy: float, qz: float, qw: float
) -> tuple[float, float, float]:
    ix = qw * vec[0] + qy * vec[2] - qz * vec[1]
    iy = qw * vec[1] + qz * vec[0] - qx * vec[2]
    iz = qw * vec[2] + qx * vec[1] - qy * vec[0]
    iw = -qx * vec[0] - qy * vec[1] - qz * vec[2]
    return (
        ix * qw + iw * -qx + iy * -qz - iz * -qy,
        iy * qw + iw * -qy + iz * -qx - ix * -qz,
        iz * qw + iw * -qz + ix * -qy - iy * -qx,
    )


def point_in_overview_frustum(
    point: tuple[float, float, float],
    *,
    eye: tuple[float, float, float],
    orientation: tuple[float, float, float, float],
    hfov_rad: float | None = None,
    vfov_rad: float | None = None,
) -> bool:
    hfov = math.radians(_OVERVIEW_HFOV_DEG) if hfov_rad is None else hfov_rad
    vfov = _overview_vertical_fov_rad() if vfov_rad is None else vfov_rad
    qx, qy, qz, qw = orientation
    # World-to-camera uses the inverse (conjugate) of the camera orientation quaternion.
    local = _rotate_vector_by_quaternion(
        (point[0] - eye[0], point[1] - eye[1], point[2] - eye[2]),
        -qx,
        -qy,
        -qz,
        qw,
    )
    inv_len = 1.0 / math.sqrt(
        local[0] * local[0] + local[1] * local[1] + local[2] * local[2] + 1e-12
    )
    lx, ly, lz = local[0] * inv_len, local[1] * inv_len, local[2] * inv_len
    if lz >= -1e-6:
        return False
    yaw_angle = math.atan2(lx, -lz)
    pitch_angle = math.atan2(ly, -lz)
    return abs(yaw_angle) <= hfov / 2.0 and abs(pitch_angle) <= vfov / 2.0


def compute_auto_overview_pose(config: DemoWaypointMissionConfig) -> OverviewPose6:
    """Place the GUI camera so the full demo path fits in a 60°×(16:9) frustum with 15% margin."""
    points = mission_overview_world_points(config)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    margin = 1.0 + _OVERVIEW_BOUND_MARGIN
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    hx = max((max(xs) - min(xs)) / 2.0 * margin, 2.0)
    hy = max((max(ys) - min(ys)) / 2.0 * margin, 2.0)
    hz = max((max(zs) - min(zs)) / 2.0 * margin, 2.0)
    h_span = math.hypot(hx, hy)
    hfov = math.radians(_OVERVIEW_HFOV_DEG)
    vfov = _overview_vertical_fov_rad()
    distance = max(h_span / math.tan(hfov / 2.0), hz / math.tan(vfov / 2.0))
    offset_dir = (
        math.cos(_OVERVIEW_ELEVATION_RAD) * math.cos(_OVERVIEW_AZIMUTH_RAD),
        math.cos(_OVERVIEW_ELEVATION_RAD) * math.sin(_OVERVIEW_AZIMUTH_RAD),
        math.sin(_OVERVIEW_ELEVATION_RAD),
    )
    for _ in range(24):
        eye = (
            cx + offset_dir[0] * distance,
            cy + offset_dir[1] * distance,
            cz + offset_dir[2] * distance,
        )
        orientation = quaternion_look_at(eye, (cx, cy, cz))
        if all(
            point_in_overview_frustum(point, eye=eye, orientation=orientation, hfov_rad=hfov, vfov_rad=vfov)
            for point in points
        ):
            roll, pitch, yaw = _quaternion_to_euler(*orientation)
            return OverviewPose6(eye[0], eye[1], eye[2], roll, pitch, yaw)
        distance *= 1.12
    orientation = quaternion_look_at(eye, (cx, cy, cz))
    roll, pitch, yaw = _quaternion_to_euler(*orientation)
    return OverviewPose6(eye[0], eye[1], eye[2], roll, pitch, yaw)


def resolve_overview_pose(
    config: DemoWaypointMissionConfig,
) -> tuple[OverviewPose6, Literal["auto", "env", "config"], str | None]:
    env_pose, env_err = parse_overview_pose_env()
    if env_err:
        return compute_auto_overview_pose(config), "auto", env_err
    if env_pose is not None:
        return env_pose, "env", None
    auto = compute_auto_overview_pose(config)
    return auto, "auto", None


def resolve_gz_world_name() -> str:
    return os.environ.get("PX4_GZ_WORLD", DEFAULT_GZ_WORLD_NAME).strip() or DEFAULT_GZ_WORLD_NAME


def resolve_follow_model_name() -> tuple[str, str | None]:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_FOLLOW_MODEL", DEFAULT_FOLLOW_MODEL_NAME).strip()
    if not raw:
        return DEFAULT_FOLLOW_MODEL_NAME, None
    if not _FOLLOW_MODEL_PATTERN.fullmatch(raw):
        return DEFAULT_FOLLOW_MODEL_NAME, "BYTEWOLF_DEMO_GZ_FOLLOW_MODEL must match ^[A-Za-z0-9_]+$"
    return raw, None


def waypoint_world_position(
    waypoint: WaypointCommand,
    *,
    spawn_world_x_m: float = BAYLANDS_DEMO_SPAWN_WORLD_X_M,
    spawn_world_y_m: float = BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
    ground_z_offset_m: float = BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
) -> tuple[float, float, float]:
    """Convert plan NED offsets (north/east) to Gazebo world coordinates."""
    world_x = spawn_world_x_m + float(waypoint.east_m)
    world_y = spawn_world_y_m + float(waypoint.north_m)
    world_z = ground_z_offset_m + float(waypoint.target_altitude_m)
    return world_x, world_y, world_z


def parse_follow_offset_env() -> tuple[tuple[float, float, float] | None, str | None]:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET", "").strip()
    if not raw:
        return DEFAULT_FOLLOW_OFFSET, None
    parts = raw.replace(",", " ").split()
    if len(parts) != 3:
        return None, "BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET must have three numbers (x y z)"
    try:
        values = tuple(float(part) for part in parts)
    except ValueError:
        return None, "BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET must be numeric"
    for axis, value in zip(("x", "y", "z"), values):
        low, high = _FOLLOW_OFFSET_LIMITS[axis]
        if not (low <= value <= high):
            return None, f"BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET {axis}={value} outside [{low}, {high}]"
    return values, None  # type: ignore[return-value]


def _gz_transport_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GZ_IP"] = "127.0.0.1"
    return env


def gz_binary() -> str | None:
    return shutil.which("gz")


@dataclass
class GzCli:
    """Thin wrapper around ``gz`` for tests."""

    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def service_info(self, service: str, *, timeout_s: float = 3.0) -> str:
        if gz_binary() is None:
            return ""
        try:
            completed = self.run(
                ["gz", "service", "-i", service],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=_gz_transport_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return str(completed.stdout or "") + str(completed.stderr or "")

    def service_list(self, *, timeout_s: float = 5.0) -> str:
        if gz_binary() is None:
            return ""
        try:
            completed = self.run(
                ["gz", "service", "-l"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=_gz_transport_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return completed.stdout or ""

    def service_call(
        self,
        service: str,
        *,
        reqtype: str,
        reptype: str,
        req: str,
        timeout_ms: int = 2000,
    ) -> tuple[bool, str | None]:
        if gz_binary() is None:
            return False, "gz binary not available"
        try:
            completed = self.run(
                [
                    "gz",
                    "service",
                    "-s",
                    service,
                    "--reqtype",
                    reqtype,
                    "--reptype",
                    reptype,
                    "--timeout",
                    str(timeout_ms),
                    "--req",
                    req,
                ],
                capture_output=True,
                text=True,
                timeout=max(2.0, timeout_ms / 1000.0 + 1.0),
                check=False,
                env=_gz_transport_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, str(error)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return False, detail or f"exit {completed.returncode}"
        return True, None

    def pose_has_model(self, world: str, model_name: str, *, timeout_s: float = 3.0) -> bool:
        topic = f"/world/{world}/pose/info"
        try:
            completed = self.run(
                ["gz", "topic", "-e", "-t", topic, "--json-output", "-n", "1"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=_gz_transport_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        for line in completed.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError:
                continue
            for pose in document.get("pose", []):
                if pose.get("name") == model_name:
                    return True
        return False


def wait_for_service(
    cli: GzCli,
    service: str,
    *,
    timeout_s: float = 15.0,
    poll_s: float = 0.25,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if service in cli.service_list(timeout_s=min(3.0, timeout_s)):
            return True
        time.sleep(poll_s)
    return False


def wait_for_model_spawned(
    cli: GzCli,
    model_name: str,
    *,
    world: str | None = None,
    timeout_s: float = 30.0,
) -> bool:
    resolved_world = world or resolve_gz_world_name()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cli.pose_has_model(resolved_world, model_name, timeout_s=3.0):
            return True
        time.sleep(0.5)
    return False


def marker_request_text(
    *,
    marker_id: int,
    world_x: float,
    world_y: float,
    world_z: float,
    color: tuple[float, float, float],
    alpha: float = 0.5,
) -> str:
    r, g, b = color
    return (
        f'ns: "{_MARKER_NAMESPACE}", id: {marker_id}, action: ADD_MODIFY, type: SPHERE, visibility: GUI, '
        f"pose: {{position: {{x: {world_x}, y: {world_y}, z: {world_z}}}}}, "
        f"scale: {{x: 0.5, y: 0.5, z: 0.5}}, "
        f"material: {{ambient: {{r: {r}, g: {g}, b: {b}, a: {alpha}}}, "
        f"diffuse: {{r: {r}, g: {g}, b: {b}, a: {alpha}}}, emissive: {{r: {r}, g: {g}, b: {b}, a: {alpha}}}, "
        f"specular: {{r: 0.1, g: 0.1, b: 0.1, a: {alpha}}}}}, "
        f"lifetime: {{sec: 0, nsec: 0}}"
    )


@dataclass
class CameraFollowEvidence:
    requested: bool = False
    ok: bool = False
    offset: dict[str, float] | None = None
    error: str | None = None
    model: str | None = None
    follow_ok: bool | None = None
    offset_ok: bool | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "requested": self.requested,
            "ok": self.ok,
        }
        if self.offset is not None:
            payload["offset"] = self.offset
        if self.error:
            payload["error"] = self.error
        if self.model:
            payload["model"] = self.model
        if self.follow_ok is not None:
            payload["follow_ok"] = self.follow_ok
        if self.offset_ok is not None:
            payload["offset_ok"] = self.offset_ok
        return payload


def resolve_move_to_pose_service_types(cli: GzCli) -> tuple[str, str]:
    """Discover /gui/move_to/pose types via bounded ``gz service -i``, else use proven defaults."""
    text = cli.service_info(_MOVE_TO_POSE_SERVICE, timeout_s=3.0)
    req_match = re.search(r"Req(?:uest)?[:\s]+([A-Za-z0-9_.]+)", text)
    rep_match = re.search(r"Rep(?:ly)?[:\s]+([A-Za-z0-9_.]+)", text)
    reqtype = req_match.group(1) if req_match else _DEFAULT_MOVE_TO_REQTYPE
    reptype = rep_match.group(1) if rep_match else _DEFAULT_MOVE_TO_REPTYPE
    return reqtype, reptype


@dataclass
class CameraSetupEvidence:
    camera_mode: str
    ok: bool = False
    requested: bool = False
    error: str | None = None
    camera_pose_used: dict[str, float] | None = None
    pose_source: str | None = None
    mode_config_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "camera_mode": self.camera_mode,
            "requested": self.requested,
            "ok": self.ok,
        }
        if self.camera_pose_used is not None:
            payload["camera_pose_used"] = self.camera_pose_used
        if self.pose_source is not None:
            payload["pose_source"] = self.pose_source
        if self.error:
            payload["error"] = self.error
        if self.mode_config_error:
            payload["mode_config_error"] = self.mode_config_error
        return payload


def guicamera_request_text(pose: OverviewPose6) -> str:
    qx, qy, qz, qw = _euler_to_quaternion(pose.roll, pose.pitch, pose.yaw)
    return (
        "pose: {"
        f"position: {{x: {pose.x}, y: {pose.y}, z: {pose.z}}}, "
        f"orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}, "
        "duration: 1"
        "}"
    )


def apply_overview_camera(
    cli: GzCli,
    pose: OverviewPose6,
    *,
    pose_source: str,
    wait_for_services_s: float = 15.0,
    mode_config_error: str | None = None,
) -> CameraSetupEvidence:
    evidence = CameraSetupEvidence(
        camera_mode="overview",
        camera_pose_used=pose.as_dict(),
        pose_source=pose_source,
        mode_config_error=mode_config_error,
    )
    if gz_binary() is None:
        evidence.error = "gz binary not available"
        return evidence
    if not wait_for_service(cli, _MOVE_TO_POSE_SERVICE, timeout_s=wait_for_services_s):
        evidence.error = f"{_MOVE_TO_POSE_SERVICE} not advertised before timeout"
        return evidence
    evidence.requested = True
    reqtype, reptype = resolve_move_to_pose_service_types(cli)
    ok, err = cli.service_call(
        _MOVE_TO_POSE_SERVICE,
        reqtype=reqtype,
        reptype=reptype,
        req=guicamera_request_text(pose),
        timeout_ms=3000,
    )
    evidence.ok = bool(ok)
    if not ok:
        evidence.error = err or "move_to/pose call failed"
    return evidence


def apply_camera_follow(
    cli: GzCli,
    *,
    model_name: str,
    offset: tuple[float, float, float],
    wait_for_services_s: float = 15.0,
) -> CameraFollowEvidence:
    evidence = CameraFollowEvidence(model=model_name, offset={"x": offset[0], "y": offset[1], "z": offset[2]})
    if gz_binary() is None:
        evidence.error = "gz binary not available"
        return evidence
    if not wait_for_service(cli, "/gui/follow", timeout_s=wait_for_services_s):
        evidence.error = "/gui/follow not advertised before timeout"
        return evidence
    if not wait_for_service(cli, "/gui/follow/offset", timeout_s=min(5.0, wait_for_services_s)):
        evidence.error = "/gui/follow/offset not advertised before timeout"
        return evidence
    evidence.requested = True
    follow_ok, follow_err = cli.service_call(
        "/gui/follow",
        reqtype="gz.msgs.StringMsg",
        reptype="gz.msgs.Boolean",
        req=f'data: "{model_name}"',
    )
    evidence.follow_ok = follow_ok
    ox, oy, oz = offset
    offset_ok, offset_err = cli.service_call(
        "/gui/follow/offset",
        reqtype="gz.msgs.Vector3d",
        reptype="gz.msgs.Boolean",
        req=f"x: {ox}, y: {oy}, z: {oz}",
    )
    evidence.offset_ok = offset_ok
    evidence.ok = bool(follow_ok and offset_ok)
    if not evidence.ok:
        parts = [part for part in (follow_err, offset_err) if part]
        evidence.error = "; ".join(parts) if parts else "follow or offset call failed"
    return evidence


@dataclass
class WaypointMarkerEvidence:
    requested: int = 0
    placed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "placed": self.placed,
            "errors": self.errors or None,
        }


def place_waypoint_markers(
    cli: GzCli,
    config: DemoWaypointMissionConfig,
    *,
    spawn_world_x_m: float = BAYLANDS_DEMO_SPAWN_WORLD_X_M,
    spawn_world_y_m: float = BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
    ground_z_offset_m: float = BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
) -> WaypointMarkerEvidence:
    evidence = WaypointMarkerEvidence(requested=len(config.waypoints))
    if gz_binary() is None:
        evidence.errors.append("gz binary not available")
        return evidence
    for index, waypoint in enumerate(config.waypoints, start=1):
        wx, wy, wz = waypoint_world_position(
            waypoint,
            spawn_world_x_m=spawn_world_x_m,
            spawn_world_y_m=spawn_world_y_m,
            ground_z_offset_m=ground_z_offset_m,
        )
        color = _WAYPOINT_MARKER_COLORS[(index - 1) % len(_WAYPOINT_MARKER_COLORS)]
        req = marker_request_text(marker_id=index, world_x=wx, world_y=wy, world_z=wz, color=color)
        ok, err = cli.service_call(
            "/marker",
            reqtype="gz.msgs.Marker",
            reptype="gz.msgs.Empty",
            req=req,
            timeout_ms=3000,
        )
        if ok:
            evidence.placed += 1
        elif err:
            evidence.errors.append(f"wp{index}: {err}")
    return evidence


_FRAME_PHASES = ("takeoff", "waypoint_2", "rtl_start", "landing")


def _parse_iso_epoch(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import UTC, datetime

    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(UTC).timestamp()
    except ValueError:
        return None


def compute_frame_phase_epochs(
    *,
    capture_started_epoch: float,
    armed_at_epoch: float | None,
    mission_items_reached: list[dict[str, object]],
    execution_events: list[dict[str, object]],
) -> dict[str, float | None]:
    """Return absolute epoch per phase name (for ffmpeg -ss from video t=0)."""
    phases: dict[str, float | None] = {name: None for name in _FRAME_PHASES}
    if armed_at_epoch is not None:
        phases["takeoff"] = armed_at_epoch + 5.0
    for item in mission_items_reached:
        if int(item.get("index", -1)) == 2:
            phases["waypoint_2"] = _parse_iso_epoch(str(item.get("timestamp")))
            break
    for event in execution_events:
        phase = event.get("phase")
        if phase == "returning":
            phases["rtl_start"] = _parse_iso_epoch(str(event.get("timestamp")))
        elif phase == "landing":
            phases["landing"] = _parse_iso_epoch(str(event.get("timestamp")))
        elif phase == "completed" and phases["landing"] is None:
            phases["landing"] = _parse_iso_epoch(str(event.get("timestamp")))
    # Convert absolute epoch -> offset from capture start
    offsets: dict[str, float | None] = {}
    for name, epoch in phases.items():
        if epoch is None or epoch < capture_started_epoch:
            offsets[name] = None
        else:
            offsets[name] = epoch - capture_started_epoch
    return offsets


def extract_review_frames(
    video_path: Path,
    frames_directory: Path,
    phase_offsets_s: dict[str, float | None],
    *,
    stamp: str,
    ffmpeg_run: Callable[..., subprocess.CompletedProcess[Any]] | None = None,
) -> list[dict[str, object]]:
    runner = ffmpeg_run or subprocess.run
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not video_path.is_file():
        return []
    frames_directory.mkdir(parents=True, exist_ok=True)
    extracted: list[dict[str, object]] = []
    for phase, offset_s in phase_offsets_s.items():
        if offset_s is None:
            continue
        filename = f"frame-{stamp}-{phase}.png"
        output = frames_directory / filename
        try:
            completed = runner(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{max(0.0, offset_s):.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    str(output),
                ],
                capture_output=True,
                check=False,
                timeout=60.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            continue
        extracted.append(
            {
                "phase": phase,
                "offset_s": round(offset_s, 3),
                "relative_path": f"frames/{filename}",
            }
        )
    return extracted


@dataclass
class DemoGzVisualDirector:
    """Orchestrates gz GUI scene setup during demo-waypoint capture (never touches MAVSDK)."""

    config: DemoWaypointMissionConfig
    cli: GzCli = field(default_factory=GzCli)
    camera_follow_history: list[dict[str, object]] = field(default_factory=list)
    camera_setup_history: list[dict[str, object]] = field(default_factory=list)
    markers: WaypointMarkerEvidence | None = None

    async def after_gui_recording_start(self) -> None:
        import asyncio

        mode, mode_err = effective_demo_gz_camera_mode()
        world = resolve_gz_world_name()
        model, _model_err = resolve_follow_model_name()
        if mode != "off":
            await asyncio.to_thread(wait_for_model_spawned, self.cli, model, world=world, timeout_s=30.0)
        if mode == "overview":
            pose, pose_source, pose_err = resolve_overview_pose(self.config)
            setup = await asyncio.to_thread(
                apply_overview_camera,
                self.cli,
                pose,
                pose_source=pose_source,
                mode_config_error=mode_err or pose_err,
            )
            self.camera_setup_history.append(setup.as_dict())
        elif mode == "follow":
            offset, offset_err = parse_follow_offset_env()
            if offset_err:
                self.camera_setup_history.append(
                    CameraSetupEvidence(camera_mode="follow", error=offset_err, mode_config_error=mode_err).as_dict()
                )
                return
            assert offset is not None
            follow = await asyncio.to_thread(apply_camera_follow, self.cli, model_name=model, offset=offset)
            self.camera_follow_history.append(follow.as_dict())
            self.camera_setup_history.append(
                {
                    **CameraSetupEvidence(
                        camera_mode="follow",
                        ok=follow.ok,
                        requested=follow.requested,
                        error=follow.error,
                        mode_config_error=mode_err,
                    ).as_dict(),
                    "follow": follow.as_dict(),
                }
            )
        elif mode == "off":
            self.camera_setup_history.append(
                CameraSetupEvidence(camera_mode="off", ok=True, mode_config_error=mode_err).as_dict()
            )
        if mode != "off":
            self.markers = await asyncio.to_thread(place_waypoint_markers, self.cli, self.config)

    async def reaffirm_camera_follow_after_arm(self) -> None:
        import asyncio

        mode, _ = effective_demo_gz_camera_mode()
        if mode != "follow":
            return
        model, model_err = resolve_follow_model_name()
        offset, offset_err = parse_follow_offset_env()
        if model_err or offset is None or offset_err:
            return
        follow = await asyncio.to_thread(apply_camera_follow, self.cli, model_name=model, offset=offset, wait_for_services_s=5.0)
        self.camera_follow_history.append({"phase": "post_arm", **follow.as_dict()})

    async def reaffirm_camera_follow_at_rtl(self) -> None:
        import asyncio

        mode, _ = effective_demo_gz_camera_mode()
        if mode != "follow":
            return
        model, model_err = resolve_follow_model_name()
        offset, offset_err = parse_follow_offset_env()
        if model_err or offset is None or offset_err:
            return
        try:
            follow = await asyncio.to_thread(
                apply_camera_follow,
                self.cli,
                model_name=model,
                offset=offset,
                wait_for_services_s=5.0,
            )
            self.camera_follow_history.append({"phase": "rtl_start", **follow.as_dict()})
        except Exception as error:
            self.camera_follow_history.append(
                {"phase": "rtl_start", "ok": False, "error": f"{type(error).__name__}: {error}"}
            )

    def build_post_capture_evidence(
        self,
        *,
        video_path: Path,
        evidence_directory: Path,
        capture_started_epoch: float,
        stamp: str,
        execution_events: list[dict[str, object]],
        mission_items_reached: list[dict[str, object]],
        armed_at_epoch: float | None,
        require_visual_review: bool = True,
    ) -> dict[str, object]:
        offsets = compute_frame_phase_epochs(
            capture_started_epoch=capture_started_epoch,
            armed_at_epoch=armed_at_epoch,
            mission_items_reached=mission_items_reached,
            execution_events=execution_events,
        )
        frames = extract_review_frames(
            video_path,
            evidence_directory / "frames",
            offsets,
            stamp=stamp,
        )
        last_setup = self.camera_setup_history[-1] if self.camera_setup_history else {}
        mode, mode_err = effective_demo_gz_camera_mode()
        payload: dict[str, object] = {
            "camera_mode": last_setup.get("camera_mode", mode),
            "camera_pose_used": last_setup.get("camera_pose_used"),
            "pose_source": last_setup.get("pose_source"),
            "camera_setup_steps": self.camera_setup_history or None,
            "camera_follow": self.camera_follow_history[-1] if self.camera_follow_history else None,
            "camera_follow_steps": self.camera_follow_history or None,
            "waypoint_markers": self.markers.as_dict() if self.markers else None,
            "spawn_origin": {
                "world_x_m": BAYLANDS_DEMO_SPAWN_WORLD_X_M,
                "world_y_m": BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
                "ground_z_offset_m": BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
            },
        }
        if mode_err and "mode_config_error" not in (last_setup or {}):
            payload["mode_config_error"] = mode_err
        setup_error = last_setup.get("error") if isinstance(last_setup, dict) else None
        if setup_error:
            payload["camera_setup_error"] = setup_error
        if require_visual_review:
            payload["visual_review_required"] = True
            payload["visual_review_frames"] = frames or None
            if frames:
                payload["visual_human_review"] = "pending"
                payload["visual_review_frame_paths"] = [frame["relative_path"] for frame in frames]
            else:
                payload["visual_review_reason"] = "frames_missing"
        return payload