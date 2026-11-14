"""Gazebo GUI helpers for demo-waypoint visual evidence (read-only transport, no flight commands)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    markers: WaypointMarkerEvidence | None = None

    async def after_gui_recording_start(self) -> None:
        import asyncio

        model, model_err = resolve_follow_model_name()
        offset, offset_err = parse_follow_offset_env()
        if model_err or offset_err:
            self.camera_follow_history.append(
                CameraFollowEvidence(error=model_err or offset_err).as_dict()
            )
            return
        assert offset is not None
        world = resolve_gz_world_name()
        await asyncio.to_thread(wait_for_model_spawned, self.cli, model, world=world, timeout_s=30.0)
        follow = await asyncio.to_thread(apply_camera_follow, self.cli, model_name=model, offset=offset)
        self.camera_follow_history.append(follow.as_dict())
        self.markers = await asyncio.to_thread(place_waypoint_markers, self.cli, self.config)

    async def reaffirm_camera_follow_after_arm(self) -> None:
        import asyncio

        model, model_err = resolve_follow_model_name()
        offset, offset_err = parse_follow_offset_env()
        if model_err or offset is None or offset_err:
            return
        follow = await asyncio.to_thread(apply_camera_follow, self.cli, model_name=model, offset=offset, wait_for_services_s=5.0)
        self.camera_follow_history.append({"phase": "post_arm", **follow.as_dict()})

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
        payload: dict[str, object] = {
            "camera_follow": self.camera_follow_history[-1] if self.camera_follow_history else None,
            "camera_follow_steps": self.camera_follow_history or None,
            "waypoint_markers": self.markers.as_dict() if self.markers else None,
            "spawn_origin": {
                "world_x_m": BAYLANDS_DEMO_SPAWN_WORLD_X_M,
                "world_y_m": BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
                "ground_z_offset_m": BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
            },
        }
        if require_visual_review:
            payload["visual_review_required"] = True
            payload["visual_review_frames"] = frames or None
            if frames:
                payload["visual_human_review"] = "pending"
                payload["visual_review_frame_paths"] = [frame["relative_path"] for frame in frames]
            else:
                payload["visual_review_reason"] = "frames_missing"
        return payload