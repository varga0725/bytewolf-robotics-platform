"""The proof that a MissionSpec on disk is the one safety approved.

The executor refuses to fly a plan file that is not accompanied by a matching
approval record, so this format *is* the boundary between "a model wrote some
JSON" and "a human-visible review approved exactly this". It lives in one
module because two producers of the same proof would eventually disagree, and
the disagreement would be resolved in favour of flying something unreviewed.

Every caller writes the plan and its approval together, and the executor checks
the file's hash against the record before it opens MAVSDK.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path


DEFAULT_PLAN_DIRECTORY = Path("simulation/artifacts/agent-missions")
APPROVAL_SCHEMA_VERSION = "nim-reviewed-plan-v0.1"


def default_plan_path(mission_id: str) -> Path:
    return DEFAULT_PLAN_DIRECTORY / f"{mission_id}.mission-spec.json"


def write_reviewed_plan(path: Path, mission_spec: dict[str, object], model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mission_spec, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    document_hash = sha256(path.read_bytes()).hexdigest()
    approval = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approved_at": datetime.now(UTC).isoformat(),
        "model": model,
        "mission_id": mission_spec["mission_id"],
        "plan_filename": path.name,
        "plan_sha256": document_hash,
        "safety_decision": "approved",
    }
    destination = approval_path(path)
    destination.write_text(json.dumps(approval, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def approval_path(plan_path: Path) -> Path:
    return plan_path.with_name(f"{plan_path.name}.approval.json")


def require_matching_review_approval(plan_path: Path, raw_document: bytes) -> None:
    record_path = approval_path(plan_path)
    try:
        approval = json.loads(record_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(
            f"Reviewed MissionSpec has no approval record: '{record_path}'."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Reviewed MissionSpec approval record is not JSON: '{record_path}'.") from error
    if not isinstance(approval, dict):
        raise RuntimeError("Reviewed MissionSpec approval record must be a JSON object.")
    if approval.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise RuntimeError("Reviewed MissionSpec approval record has an unsupported schema version.")
    if approval.get("safety_decision") != "approved":
        raise RuntimeError("Reviewed MissionSpec approval record is not approved.")
    if approval.get("plan_filename") != plan_path.name:
        raise RuntimeError("Reviewed MissionSpec approval record is for a different plan file.")
    if approval.get("plan_sha256") != sha256(raw_document).hexdigest():
        raise RuntimeError("Reviewed MissionSpec differs from the safety-approved plan.")
