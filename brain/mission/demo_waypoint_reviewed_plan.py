"""Reviewed on-disk proof for the demo-waypoint-mission type."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from brain.mission.demo_waypoint_mission import (
    MISSION_TYPE,
    DemoWaypointMissionConfig,
    mission_items_document,
)

DEFAULT_DEMO_WAYPOINT_PLAN_DIRECTORY = Path("simulation/artifacts/agent-missions")
DEMO_WAYPOINT_PLAN_SCHEMA_VERSION = "demo-waypoint-mission.v0.1"
DEMO_WAYPOINT_APPROVAL_SCHEMA_VERSION = "demo-waypoint-mission-approval.v0.1"


def default_demo_waypoint_plan_path(mission_id: str) -> Path:
    return DEFAULT_DEMO_WAYPOINT_PLAN_DIRECTORY / f"{mission_id}.demo-waypoint-mission.json"


def write_reviewed_demo_waypoint_plan(
    path: Path, config: DemoWaypointMissionConfig, *, mission_id: str | None = None
) -> str:
    mission_id = mission_id or str(uuid4())
    document = {
        "schema_version": DEMO_WAYPOINT_PLAN_SCHEMA_VERSION,
        "mission_type": MISSION_TYPE,
        "mission_id": mission_id,
        "takeoff_altitude_m": config.takeoff_altitude_m,
        "waypoints": [
            {
                "north_m": waypoint.north_m,
                "east_m": waypoint.east_m,
                "relative_altitude_m": waypoint.target_altitude_m,
            }
            for waypoint in config.waypoints
        ],
        "cruise_speed_m_s": config.cruise_speed_m_s,
        "acceptance_radius_m": config.acceptance_radius_m,
        "max_mission_items": config.max_mission_items,
        "upload_timeout_s": config.upload_timeout_s,
        "mission_timeout_s": config.mission_timeout_s,
        "progress_stall_timeout_s": config.progress_stall_timeout_s,
        "landing_timeout_s": config.landing_timeout_s,
        "home_tolerance_m": config.home_tolerance_m,
        "rtl_timeout_s": config.rtl_timeout_s,
        "mission_items_preview": mission_items_document(config),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    approval = {
        "schema_version": DEMO_WAYPOINT_APPROVAL_SCHEMA_VERSION,
        "approved_at": datetime.now(UTC).isoformat(),
        "mission_type": MISSION_TYPE,
        "mission_id": mission_id,
        "plan_filename": path.name,
        "plan_sha256": sha256(path.read_bytes()).hexdigest(),
        "safety_decision": "approved",
    }
    path.with_name(f"{path.name}.approval.json").write_text(
        json.dumps(approval, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return mission_id


def write_operator_approval_for_plan(plan_path: Path) -> None:
    """Write the sha256-bound approval sidecar after explicit operator confirmation."""
    if not plan_path.is_file():
        raise RuntimeError(f"Reviewed demo plan file '{plan_path}' does not exist.")
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    if document.get("mission_type") != MISSION_TYPE:
        raise RuntimeError("Plan is not a demo-waypoint-mission.")
    mission_id = str(document.get("mission_id", ""))
    approval = {
        "schema_version": DEMO_WAYPOINT_APPROVAL_SCHEMA_VERSION,
        "approved_at": datetime.now(UTC).isoformat(),
        "mission_type": MISSION_TYPE,
        "mission_id": mission_id,
        "plan_filename": plan_path.name,
        "plan_sha256": sha256(plan_path.read_bytes()).hexdigest(),
        "safety_decision": "approved",
        "approval_channel": "operator-cli",
    }
    plan_path.with_name(f"{plan_path.name}.approval.json").write_text(
        json.dumps(approval, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def require_matching_demo_waypoint_approval(plan_path: Path) -> dict[str, object]:
    try:
        approval = json.loads(plan_path.with_name(f"{plan_path.name}.approval.json").read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"Reviewed demo plan has no approval record for '{plan_path}'.") from error
    if approval.get("schema_version") != DEMO_WAYPOINT_APPROVAL_SCHEMA_VERSION:
        raise RuntimeError("Demo waypoint plan approval record has an unsupported schema version.")
    if approval.get("safety_decision") != "approved":
        raise RuntimeError("Demo waypoint plan approval record is not approved.")
    if approval.get("plan_sha256") != sha256(plan_path.read_bytes()).hexdigest():
        raise RuntimeError("Demo waypoint plan differs from the approved document.")
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    if document.get("mission_type") != MISSION_TYPE:
        raise RuntimeError("Plan is not a demo-waypoint-mission.")
    return document
