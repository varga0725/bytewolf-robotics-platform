"""Monday-demo SITL route: MAVSDK Mission plugin loop, then PX4 RTL and disarm."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.mission.demo_errors import DemoWaypointMissionError
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.simulation_target import redact_loopback_endpoint
from brain.safety.gate import SafetyGate, SafetyViolation
from brain.safety.profile import SafetyProfile

MISSION_TYPE = "demo-waypoint-mission"
DEFAULT_MAX_MISSION_ITEMS = 12
MIN_RELATIVE_ALTITUDE_M = 1.0
MIN_ACCEPTANCE_RADIUS_M = 0.5
MAX_ACCEPTANCE_RADIUS_M = 25.0
MIN_CRUISE_SPEED_M_S = 0.5


@dataclass(frozen=True)
class DemoWaypointMissionConfig:
    """Bounded multi-waypoint demo using the MAVSDK Mission plugin."""

    takeoff_altitude_m: float
    waypoints: tuple[WaypointCommand, ...]
    cruise_speed_m_s: float = 3.0
    acceptance_radius_m: float = 2.0
    max_mission_items: int = DEFAULT_MAX_MISSION_ITEMS
    upload_timeout_s: float = 30.0
    mission_timeout_s: float = 300.0
    progress_stall_timeout_s: float = 60.0
    landing_timeout_s: float = 120.0
    home_tolerance_m: float = 6.0
    rtl_timeout_s: float = 180.0


def default_demo_waypoint_loop(
    *, altitude_m: float = 8.0, leg_m: float = 8.0
) -> tuple[WaypointCommand, ...]:
    """Conservative square loop inside typical twin geofence limits."""
    return demo_waypoint_loop_for_count(4, altitude_m=altitude_m, leg_m=leg_m)


def demo_waypoint_loop_for_count(
    waypoint_count: int, *, altitude_m: float = 8.0, leg_m: float = 8.0
) -> tuple[WaypointCommand, ...]:
    """Place ``waypoint_count`` corners cycling the default square."""
    if waypoint_count < 1:
        raise DemoWaypointMissionError("Waypoint count must be at least 1.")
    square = (
        (leg_m, 0.0),
        (leg_m, leg_m),
        (0.0, leg_m),
        (0.0, 0.0),
    )
    return tuple(
        WaypointCommand(north_m=north, east_m=east, target_altitude_m=altitude_m)
        for north, east in (square[index % len(square)] for index in range(waypoint_count))
    )


def max_demo_mission_items_for_profile(profile: SafetyProfile) -> int:
    """Cap mission items from the twin radius so geofence distance cannot be exceeded trivially."""
    leg_budget = max(1, int(profile.max_radius_m // 8))
    return min(DEFAULT_MAX_MISSION_ITEMS, max(2, leg_budget + 1))


def load_demo_waypoint_mission_config(
    environment: os._Environ[str] | None = None,
    *,
    profile: SafetyProfile | None = None,
) -> DemoWaypointMissionConfig:
    values = os.environ if environment is None else environment

    def _read(name: str, default: float) -> float:
        raw = values.get(name, str(default)).strip()
        try:
            return float(raw)
        except ValueError as error:
            raise DemoWaypointMissionError(f"{name} must be a finite number.") from error

    def _read_int(name: str, default: int) -> int:
        raw = values.get(name, str(default)).strip()
        try:
            return int(raw)
        except ValueError as error:
            raise DemoWaypointMissionError(f"{name} must be an integer.") from error

    altitude_m = _read("BYTEWOLF_DEMO_WAYPOINT_ALTITUDE_M", 8.0)
    leg_m = _read("BYTEWOLF_DEMO_WAYPOINT_LEG_M", 8.0)
    max_items = DEFAULT_MAX_MISSION_ITEMS
    if profile is not None:
        max_items = max_demo_mission_items_for_profile(profile)
    elif values.get("BYTEWOLF_DEMO_WAYPOINT_MAX_ITEMS"):
        max_items = _read_int("BYTEWOLF_DEMO_WAYPOINT_MAX_ITEMS", DEFAULT_MAX_MISSION_ITEMS)
    waypoint_count = _read_int("BYTEWOLF_DEMO_WAYPOINT_COUNT", 4)
    max_waypoints = max_items - 1
    if waypoint_count < 1 or waypoint_count > max_waypoints:
        raise DemoWaypointMissionError(
            f"BYTEWOLF_DEMO_WAYPOINT_COUNT must be between 1 and {max_waypoints} for this safety profile."
        )
    waypoints = demo_waypoint_loop_for_count(waypoint_count, altitude_m=altitude_m, leg_m=leg_m)

    return DemoWaypointMissionConfig(
        takeoff_altitude_m=_read("BYTEWOLF_DEMO_WAYPOINT_TAKEOFF_ALTITUDE_M", altitude_m),
        waypoints=waypoints,
        cruise_speed_m_s=_read("BYTEWOLF_DEMO_WAYPOINT_SPEED_M_S", 3.0),
        acceptance_radius_m=_read("BYTEWOLF_DEMO_WAYPOINT_ACCEPTANCE_RADIUS_M", 2.0),
        max_mission_items=max_items,
        upload_timeout_s=_read("BYTEWOLF_DEMO_WAYPOINT_UPLOAD_TIMEOUT_S", 30.0),
        mission_timeout_s=_read("BYTEWOLF_DEMO_WAYPOINT_MISSION_TIMEOUT_S", 300.0),
        progress_stall_timeout_s=_read("BYTEWOLF_DEMO_WAYPOINT_PROGRESS_STALL_S", 60.0),
        landing_timeout_s=_read("BYTEWOLF_DEMO_WAYPOINT_LANDING_TIMEOUT_S", 120.0),
        home_tolerance_m=_read("BYTEWOLF_DEMO_WAYPOINT_HOME_TOLERANCE_M", 6.0),
        rtl_timeout_s=_read("BYTEWOLF_DEMO_WAYPOINT_RTL_TIMEOUT_S", 180.0),
    )


def authorize_demo_waypoint_mission(
    gate: SafetyGate,
    config: DemoWaypointMissionConfig,
    *,
    profile: SafetyProfile | None = None,
) -> DemoWaypointMissionConfig:
    """Validate takeoff and every mission item before any upload reaches MAVSDK."""
    planned_items = 1 + len(config.waypoints)
    if planned_items > config.max_mission_items:
        raise SafetyViolation(
            f"Mission item count {planned_items} exceeds the limit of {config.max_mission_items}."
        )
    if config.cruise_speed_m_s < MIN_CRUISE_SPEED_M_S:
        raise SafetyViolation(
            f"Cruise speed {config.cruise_speed_m_s:g} m/s is below the minimum {MIN_CRUISE_SPEED_M_S:g} m/s."
        )
    if profile is not None and config.cruise_speed_m_s > profile.max_speed_m_s:
        raise SafetyViolation(
            f"Cruise speed {config.cruise_speed_m_s:g} m/s exceeds the twin limit of {profile.max_speed_m_s:g} m/s."
        )
    if not (MIN_ACCEPTANCE_RADIUS_M <= config.acceptance_radius_m <= MAX_ACCEPTANCE_RADIUS_M):
        raise SafetyViolation(
            "Acceptance radius must be between "
            f"{MIN_ACCEPTANCE_RADIUS_M:g} m and {MAX_ACCEPTANCE_RADIUS_M:g} m."
        )
    if config.takeoff_altitude_m < MIN_RELATIVE_ALTITUDE_M:
        raise SafetyViolation(
            f"Takeoff altitude must be at least {MIN_RELATIVE_ALTITUDE_M:g} m."
        )
    gate.evaluate(TakeoffCommand(config.takeoff_altitude_m))
    for index, waypoint in enumerate(config.waypoints):
        if waypoint.target_altitude_m < MIN_RELATIVE_ALTITUDE_M:
            raise SafetyViolation(
                f"Mission item {index + 1}: altitude must be at least {MIN_RELATIVE_ALTITUDE_M:g} m."
            )
        try:
            gate.evaluate(waypoint)
        except SafetyViolation as error:
            raise SafetyViolation(f"Mission item {index + 1}: {error}") from error
    return config


def expected_demo_waypoint_phases() -> tuple[MissionPhase, ...]:
    return (
        MissionPhase.TAKING_OFF,
        MissionPhase.NAVIGATING,
        MissionPhase.RETURNING,
        MissionPhase.COMPLETED,
    )


def validate_demo_waypoint_mission_progress(
    execution: MissionExecution,
    *,
    items_reached: tuple[dict[str, object], ...],
    expected_item_count: int = 0,
) -> None:
    observed = tuple(event.phase for event in execution.events)
    expected = expected_demo_waypoint_phases()
    missing = [phase.value for phase in expected if phase not in observed]
    if missing:
        raise DemoWaypointMissionError(
            f"Demo waypoint mission did not reach required phases: missing {', '.join(missing)} "
            f"(observed {' -> '.join(phase.value for phase in observed)})."
        )
    if not items_reached:
        raise DemoWaypointMissionError("Mission progress produced no reached-item timestamps.")
    last = items_reached[-1]
    total = int(last.get("total", 0))
    current = int(last.get("index", -1))
    if total <= 0 or current < total:
        raise DemoWaypointMissionError(
            f"Mission progress incomplete at completion (current={current}, total={total})."
        )
    if execution.phase is not MissionPhase.COMPLETED:
        raise DemoWaypointMissionError(
            f"Mission must end in completed phase, got {execution.phase.value if execution.phase else 'none'}."
        )


def mission_items_document(config: DemoWaypointMissionConfig) -> list[dict[str, object]]:
    items: list[dict[str, object]] = [
        {
            "index": 0,
            "kind": "takeoff",
            "north_m": 0.0,
            "east_m": 0.0,
            "relative_altitude_m": config.takeoff_altitude_m,
        }
    ]
    for offset, waypoint in enumerate(config.waypoints, start=1):
        items.append(
            {
                "index": offset,
                "kind": "waypoint",
                "north_m": waypoint.north_m,
                "east_m": waypoint.east_m,
                "relative_altitude_m": waypoint.target_altitude_m,
            }
        )
    return items


def demo_waypoint_mission_evidence_document(
    *,
    execution: MissionExecution,
    config: DemoWaypointMissionConfig,
    outcome: str,
    endpoint: str,
    px4_version: str,
    baseline_profile: str,
    baseline_px4_commit: str,
    baseline_gazebo_version: str,
    simulator_name: str,
    sitl_verification: dict[str, object],
    positions: list[dict[str, object]],
    mission_items: list[dict[str, object]],
    items_reached: list[dict[str, object]],
    peak_relative_altitude_m: float | None,
    disarm_reason: str | None,
    px4_ulog_relative_path: str | None,
    trajectory_png_relative_path: str | None,
    optional_screen_capture_relative_path: str | None,
    screen_capture_status: dict[str, object] | None = None,
    started_at: datetime | None = None,
) -> dict[str, object]:
    document: dict[str, Any] = {
        "schema_version": "demo-waypoint-mission-evidence.v0.1",
        "mission_type": MISSION_TYPE,
        "started_at": (started_at or datetime.now(UTC)).isoformat(),
        "overall_status": "passed" if outcome == "completed" else "failed",
        "mavlink_endpoint": redact_loopback_endpoint(endpoint),
        "mission_items": mission_items,
        "items_reached": items_reached,
        "phase_transitions": [
            {"phase": event.phase.value, "timestamp": event.timestamp.isoformat()}
            for event in execution.events
        ],
        "position_samples_hz": 1.0,
        "positions": positions,
        "peak_relative_altitude_m": peak_relative_altitude_m,
        "disarm_reason": disarm_reason,
        "px4_version": px4_version,
        "simulator": {"name": simulator_name, "gazebo_version": baseline_gazebo_version},
        "sitl_verification": sitl_verification,
        "baseline": {
            "profile": baseline_profile,
            "px4_commit": baseline_px4_commit,
            "gazebo_sim_version": baseline_gazebo_version,
        },
        "px4_ulog_relative_path": px4_ulog_relative_path,
        "trajectory_png_relative_path": trajectory_png_relative_path,
    }
    if optional_screen_capture_relative_path is not None:
        document["optional_screen_capture_relative_path"] = optional_screen_capture_relative_path
    if screen_capture_status is not None:
        document["screen_capture"] = screen_capture_status
    return document


def _screenshot_counts_as_visual_evidence(screen: dict[str, object]) -> bool:
    path = screen.get("screenshot_relative_path") or screen.get("screen_capture_screenshot_relative_path")
    if not isinstance(path, str) or not path.strip():
        return False
    if screen.get("screenshot_gui_alive") is not True:
        return False
    if screen.get("screenshot_non_uniform") is not True:
        return False
    return True


def _visual_human_review_satisfied(document: dict[str, object]) -> bool:
    if document.get("visual_review_required") is not True:
        return True
    review = document.get("visual_human_review")
    if review == "approved":
        return True
    screen = document.get("screen_capture")
    if isinstance(screen, dict) and screen.get("visual_human_review") == "approved":
        return True
    return False


def _document_has_visual_evidence(document: dict[str, object]) -> bool:
    if not _visual_human_review_satisfied(document):
        return False
    screen = document.get("screen_capture")
    if isinstance(screen, dict) and screen.get("status") == "captured":
        return True
    if isinstance(screen, dict) and _screenshot_counts_as_visual_evidence(screen):
        return True
    top_level = {
        "screenshot_relative_path": document.get("screen_capture_screenshot_relative_path"),
        "screenshot_gui_alive": document.get("screen_capture_screenshot_gui_alive"),
        "screenshot_non_uniform": document.get("screen_capture_screenshot_non_uniform"),
    }
    return _screenshot_counts_as_visual_evidence(top_level)


def acceptance_run_is_clean(
    document: dict[str, object],
    *,
    require_visual_evidence: bool = True,
) -> bool:
    """A consecutive acceptance run needs completed outcome, ULog proof, and strict disarm/capture proof."""
    if document.get("overall_status") != "passed":
        return False
    ulog_path = document.get("px4_ulog_relative_path")
    if not ulog_path or document.get("ulog_status") == "missing":
        return False
    if not document.get("trajectory_png_relative_path"):
        return False
    disarm_reason = document.get("disarm_reason")
    if disarm_reason:
        return False
    if require_visual_evidence and not _document_has_visual_evidence(document):
        return False
    return True


def load_demo_waypoint_plan_config(plan_path: Path) -> DemoWaypointMissionConfig:
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    waypoints = tuple(
        WaypointCommand(
            north_m=float(item["north_m"]),
            east_m=float(item["east_m"]),
            target_altitude_m=float(item["relative_altitude_m"]),
        )
        for item in document["waypoints"]
    )
    return DemoWaypointMissionConfig(
        takeoff_altitude_m=float(document["takeoff_altitude_m"]),
        waypoints=waypoints,
        cruise_speed_m_s=float(document.get("cruise_speed_m_s", 3.0)),
        acceptance_radius_m=float(document.get("acceptance_radius_m", 2.0)),
        max_mission_items=int(document.get("max_mission_items", DEFAULT_MAX_MISSION_ITEMS)),
        upload_timeout_s=float(document.get("upload_timeout_s", 30.0)),
        mission_timeout_s=float(document.get("mission_timeout_s", 300.0)),
        progress_stall_timeout_s=float(document.get("progress_stall_timeout_s", 60.0)),
        landing_timeout_s=float(document.get("landing_timeout_s", 120.0)),
        home_tolerance_m=float(document.get("home_tolerance_m", 6.0)),
        rtl_timeout_s=float(document.get("rtl_timeout_s", 180.0)),
    )


def consecutive_acceptance_summary(
    run_documents: list[dict[str, object]],
    *,
    required_runs: int,
    require_visual_evidence: bool = True,
) -> dict[str, object]:
    statuses = [
        "passed" if acceptance_run_is_clean(document, require_visual_evidence=require_visual_evidence) else "failed"
        for document in run_documents
    ]
    passed = len(statuses) == required_runs and all(status == "passed" for status in statuses)
    visual_frame_paths: list[str] = []
    for document in run_documents:
        paths = document.get("visual_review_frame_paths")
        if isinstance(paths, list):
            visual_frame_paths.extend(str(path) for path in paths)
        screen = document.get("screen_capture")
        if isinstance(screen, dict):
            nested = screen.get("visual_review_frame_paths")
            if isinstance(nested, list):
                visual_frame_paths.extend(str(path) for path in nested)
    summary: dict[str, object] = {
        "schema_version": "demo-waypoint-mission-acceptance.v0.1",
        "required_consecutive_runs": required_runs,
        "runs_recorded": len(run_documents),
        "overall_status": "passed" if passed else "failed",
        "per_run_status": statuses,
        "runs": run_documents,
    }
    if visual_frame_paths:
        summary["visual_review_frame_paths"] = visual_frame_paths
        summary["visual_review_required"] = True
        summary["operator_note"] = (
            "Human frame review is required before visual evidence counts toward acceptance. "
            "Set visual_human_review to approved on each run document after reviewing extracted PNGs."
        )
    return summary
