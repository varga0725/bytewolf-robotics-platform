"""Read-only summaries of durable P0 and P1 simulation evidence."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


class SimulationEvidenceError(ValueError):
    """Raised when a caller attempts to inspect artifacts outside the workspace."""


def summarize_latest_evidence(
    artifact_root: Path | None = None,
    *,
    workspace_root: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Return deterministic, read-only evidence suitable for a Notion handoff.

    Only passed P0 repeatability reports are considered valid P0 evidence.  The
    optional P1 dashboard snapshot is reported as live, stale, missing, or
    invalid; it is never treated as a flight-control input.
    """
    workspace = (workspace_root or Path(__file__).resolve().parents[1]).resolve()
    root = (artifact_root or workspace / "simulation" / "artifacts").resolve()
    if not _is_within(root, workspace):
        raise SimulationEvidenceError("artifact_root must be within workspace_root.")

    current_time = (now or (lambda: datetime.now(UTC)))().astimezone(UTC)
    return {
        "generated_at": current_time.isoformat().replace("+00:00", "Z"),
        "p0": _latest_p0_evidence(root),
        "p1_dashboard": _dashboard_evidence(root, current_time),
    }


def _latest_p0_evidence(artifact_root: Path) -> dict[str, object]:
    candidates: list[tuple[datetime, Path, Mapping[str, Any]]] = []
    for path in sorted((artifact_root / "headless").glob("p0-repeatability-*.json")):
        document = _load_json_object(path)
        timestamp = _valid_p0_timestamp(document)
        if timestamp is not None:
            candidates.append((timestamp, path, document))
    if not candidates:
        return {"status": "missing"}

    _, path, document = max(candidates, key=lambda candidate: (candidate[0], str(candidate[1])))
    return {
        "status": "passed",
        "report": str(path.relative_to(artifact_root)),
        "started_at": _isoformat(document["started_at"]),
        "repetitions": document["repetitions"],
        "minimum_success_rate": document["minimum_success_rate"],
        "nominal_scenarios": document["nominal_scenarios"],
        "success_rates": document["success_rates"],
    }


def _dashboard_evidence(artifact_root: Path, current_time: datetime) -> dict[str, object]:
    snapshot_path = artifact_root / "dashboard" / "live-telemetry.json"
    document = _load_json_object(snapshot_path)
    if document is None:
        return {"status": "missing"}
    try:
        captured_at = _parse_timestamp(document["captured_at"])
        position = _object(document["position"])
        battery = _object(document["battery"])
        _finite_number(position["latitude_deg"])
        _finite_number(position["longitude_deg"])
        _finite_number(position["absolute_altitude_m"])
        remaining_percent = _finite_number(battery["remaining_percent"])
        if not 0.0 <= remaining_percent <= 100.0 or type(document["in_air"]) is not bool:
            raise ValueError("dashboard snapshot violates its read-only contract")
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid", "snapshot": str(snapshot_path.relative_to(artifact_root))}

    age_seconds = max(0, int((current_time - captured_at).total_seconds()))
    return {
        "status": "live" if age_seconds <= 10 else "stale",
        "snapshot": str(snapshot_path.relative_to(artifact_root)),
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "age_seconds": age_seconds,
        "in_air": document["in_air"],
    }


def _valid_p0_timestamp(document: Mapping[str, Any] | None) -> datetime | None:
    if document is None or document.get("overall_status") != "passed":
        return None
    required = ("started_at", "repetitions", "minimum_success_rate", "nominal_scenarios", "success_rates")
    if any(key not in document for key in required):
        return None
    if not isinstance(document["repetitions"], int) or document["repetitions"] <= 0:
        return None
    if not isinstance(document["minimum_success_rate"], (int, float)):
        return None
    if not isinstance(document["nominal_scenarios"], list) or not isinstance(document["success_rates"], dict):
        return None
    try:
        timestamp = _parse_timestamp(document["started_at"])
        success_rates = document["success_rates"]
        if not all(isinstance(name, str) and 0.0 <= _finite_number(rate) <= 1.0 for name, rate in success_rates.items()):
            return None
    except (TypeError, ValueError):
        return None
    return timestamp


def _load_json_object(path: Path) -> Mapping[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp.astimezone(UTC)


def _isoformat(value: object) -> str:
    return _parse_timestamp(value).isoformat().replace("+00:00", "Z")


def _object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _finite_number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("expected finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("expected finite number")
    return number


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main(arguments: tuple[str, ...] | None = None) -> None:
    """Print the latest evidence without starting SITL or changing any artifact."""
    parser = argparse.ArgumentParser(description="Summarize read-only local simulation evidence.")
    parser.add_argument("--artifact-root", type=Path, default=None)
    parsed = parser.parse_args(arguments)
    print(json.dumps(summarize_latest_evidence(parsed.artifact_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
