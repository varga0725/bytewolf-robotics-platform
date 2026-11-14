"""Post-run checks that a scenario actually exercised its safety contract."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.scenarios.sitl_startup import SITL_LAUNCH_FAILED

_SCENARIOS_MIN_PEAK_RELATIVE_ALTITUDE_M: dict[str, float] = {
    "takeoff-hover-land": 1.5,
    "takeoff-2m-hover-10s-land": 1.5,
    "mission-interrupt-hold-cleanup-land": 1.5,
    "mission-interrupt-land": 1.5,
}


def latest_artifact(directory: Path) -> Path | None:
    artifacts = tuple(directory.glob("*.json")) if directory.exists() else ()
    return artifacts[0] if len(artifacts) == 1 else None


def scenario_contract_violation(
    *,
    scenario_identifier: str,
    fallback_expectation: str,
    stdout: str,
    stderr: str,
    artifact_directory: Path,
) -> str | None:
    """Return a failure reason when a superficial pass missed the real contract."""
    peak_violation = _peak_altitude_violation(scenario_identifier, artifact_directory)
    if peak_violation is not None:
        return peak_violation
    if fallback_expectation != "land-once-after-waypoint-timeout":
        return None
    if stderr.startswith(SITL_LAUNCH_FAILED) or f"{SITL_LAUNCH_FAILED}:" in stderr:
        return "Scenario contract failed: SITL launcher exited before MAVLink was available."
    combined = f"{stdout}\n{stderr}".lower()
    if "failed to bind server to port" in combined or "connection timed out" in combined:
        return "Scenario contract failed: MAVSDK could not connect; this is not a waypoint-timeout fallback."
    artifact = latest_artifact(artifact_directory)
    if artifact is None:
        return "Scenario contract failed: no mission artifact was recorded."
    document = json.loads(artifact.read_text(encoding="utf-8"))
    phases = tuple(event.get("phase") for event in document.get("events", []))
    required = ("arming", "taking_off", "navigating", "landing")
    if not all(phase in phases for phase in required):
        return (
            "Scenario contract failed: expected in-flight waypoint timeout with one landing "
            f"fallback, observed phases {list(phases)}."
        )
    failure_reason = str(document.get("failure_reason") or "")
    if "arming" not in phases:
        return "Scenario contract failed: the vehicle never armed."
    if phases.index("landing") <= phases.index("navigating"):
        return "Scenario contract failed: landing must follow an in-flight navigating phase."
    if not failure_reason and document.get("outcome") != "failed":
        return "Scenario contract failed: the mission must fail after the waypoint timeout fallback."
    return None


def _peak_altitude_violation(scenario_identifier: str, artifact_directory: Path) -> str | None:
    minimum_peak_m = _SCENARIOS_MIN_PEAK_RELATIVE_ALTITUDE_M.get(scenario_identifier)
    if minimum_peak_m is None:
        return None
    artifact = latest_artifact(artifact_directory)
    if artifact is None:
        return "Scenario contract failed: no mission artifact was recorded."
    document = json.loads(artifact.read_text(encoding="utf-8"))
    peak = document.get("peak_relative_altitude_m")
    if peak is None:
        return (
            "Scenario contract failed: mission artifact has no peak_relative_altitude_m evidence."
        )
    try:
        peak_m = float(peak)
    except (TypeError, ValueError):
        return "Scenario contract failed: peak_relative_altitude_m is not a number."
    if peak_m < minimum_peak_m:
        return (
            "Scenario contract failed: peak relative altitude "
            f"{peak_m:.2f} m is below the required {minimum_peak_m:.2f} m."
        )
    return None
