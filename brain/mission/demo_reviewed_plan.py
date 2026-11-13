"""Reviewed on-disk proof for the demo-point-rtl mission type."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from brain.mission.demo_point_rtl import MISSION_TYPE, DemoPointRtlConfig

DEFAULT_DEMO_PLAN_DIRECTORY = Path("simulation/artifacts/agent-missions")
DEMO_PLAN_SCHEMA_VERSION = "demo-point-rtl.v0.1"
DEMO_APPROVAL_SCHEMA_VERSION = "demo-point-rtl-approval.v0.1"


def default_demo_plan_path(mission_id: str) -> Path:
    return DEFAULT_DEMO_PLAN_DIRECTORY / f"{mission_id}.demo-point-rtl.json"


def write_reviewed_demo_plan(path: Path, config: DemoPointRtlConfig, *, mission_id: str | None = None) -> str:
    mission_id = mission_id or str(uuid4())
    document = {
        "schema_version": DEMO_PLAN_SCHEMA_VERSION,
        "mission_type": MISSION_TYPE,
        "mission_id": mission_id,
        "takeoff_altitude_m": config.takeoff_altitude_m,
        "north_m": config.north_m,
        "east_m": config.east_m,
        "waypoint_altitude_m": config.waypoint_altitude_m,
        "hover_duration_s": config.hover_duration_s,
        "waypoint_tolerance_m": config.waypoint_tolerance_m,
        "waypoint_timeout_s": config.waypoint_timeout_s,
        "landing_timeout_s": config.landing_timeout_s,
        "home_tolerance_m": config.home_tolerance_m,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    approval = {
        "schema_version": DEMO_APPROVAL_SCHEMA_VERSION,
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


def require_matching_demo_approval(plan_path: Path) -> dict[str, object]:
    try:
        approval = json.loads(plan_path.with_name(f"{plan_path.name}.approval.json").read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"Reviewed demo plan has no approval record for '{plan_path}'.") from error
    if approval.get("schema_version") != DEMO_APPROVAL_SCHEMA_VERSION:
        raise RuntimeError("Demo plan approval record has an unsupported schema version.")
    if approval.get("safety_decision") != "approved":
        raise RuntimeError("Demo plan approval record is not approved.")
    if approval.get("plan_sha256") != sha256(plan_path.read_bytes()).hexdigest():
        raise RuntimeError("Demo plan differs from the approved document.")
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    if document.get("mission_type") != MISSION_TYPE:
        raise RuntimeError("Plan is not a demo-point-rtl mission.")
    return document
