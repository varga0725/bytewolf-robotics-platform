"""Monday-demo SITL route: one configured point, then PX4 return-to-launch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from brain.mission.commands import WaypointCommand
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import (
    TakeoffWaypointsReturnToHomeMission,
    authorize_takeoff_waypoints_return_to_home,
)
from brain.safety.gate import SafetyGate
from brain.mission.demo_errors import DemoPointRtlError
from brain.mission.simulation_target import redact_loopback_endpoint


MISSION_TYPE = "demo-point-rtl"


@dataclass(frozen=True)
class DemoPointRtlConfig:
    """Bounded coordinates for the one demo waypoint."""

    takeoff_altitude_m: float
    north_m: float
    east_m: float
    waypoint_altitude_m: float
    hover_duration_s: float = 0.0
    waypoint_tolerance_m: float = 2.0
    waypoint_timeout_s: float = 90.0
    landing_timeout_s: float = 120.0
    home_tolerance_m: float = 6.0


def load_demo_point_rtl_config(environment: os._Environ[str] | None = None) -> DemoPointRtlConfig:
    values = os.environ if environment is None else environment

    def _read(name: str, default: float) -> float:
        raw = values.get(name, str(default)).strip()
        try:
            return float(raw)
        except ValueError as error:
            raise DemoPointRtlError(f"{name} must be a finite number.") from error

    return DemoPointRtlConfig(
        takeoff_altitude_m=_read("BYTEWOLF_DEMO_POINT_TAKEOFF_ALTITUDE_M", 2.0),
        north_m=_read("BYTEWOLF_DEMO_POINT_NORTH_M", 8.0),
        east_m=_read("BYTEWOLF_DEMO_POINT_EAST_M", 0.0),
        waypoint_altitude_m=_read("BYTEWOLF_DEMO_POINT_ALTITUDE_M", 2.0),
        waypoint_tolerance_m=_read("BYTEWOLF_DEMO_POINT_TOLERANCE_M", 2.0),
        waypoint_timeout_s=_read("BYTEWOLF_DEMO_POINT_TIMEOUT_S", 90.0),
    )


def authorize_demo_point_rtl(
    gate: SafetyGate, config: DemoPointRtlConfig
) -> TakeoffWaypointsReturnToHomeMission:
    waypoint = WaypointCommand(
        north_m=config.north_m,
        east_m=config.east_m,
        target_altitude_m=config.waypoint_altitude_m,
    )
    return authorize_takeoff_waypoints_return_to_home(
        gate,
        takeoff_altitude_m=config.takeoff_altitude_m,
        waypoints=(waypoint,),
        hover_duration_s=config.hover_duration_s,
        waypoint_tolerance_m=config.waypoint_tolerance_m,
        waypoint_timeout_s=config.waypoint_timeout_s,
        landing_timeout_s=config.landing_timeout_s,
        home_tolerance_m=config.home_tolerance_m,
    )


def expected_demo_point_rtl_phases(*, hover_duration_s: float) -> tuple[MissionPhase, ...]:
    phases = [MissionPhase.TAKING_OFF, MissionPhase.NAVIGATING]
    if hover_duration_s > 0.0:
        phases.append(MissionPhase.HOVERING)
    phases.extend((MissionPhase.RETURNING, MissionPhase.COMPLETED))
    return tuple(phases)


def validate_demo_point_rtl_progress(
    execution: MissionExecution, *, hover_duration_s: float
) -> None:
    observed = tuple(event.phase for event in execution.events)
    expected = expected_demo_point_rtl_phases(hover_duration_s=hover_duration_s)
    missing = [phase.value for phase in expected if phase not in observed]
    if missing:
        raise DemoPointRtlError(
            f"Demo mission did not reach required phases: missing {', '.join(missing)} "
            f"(observed {' -> '.join(phase.value for phase in observed)})."
        )


def demo_point_rtl_evidence_document(
    *,
    execution: MissionExecution,
    config: DemoPointRtlConfig,
    outcome: str,
    endpoint: str,
    px4_version: str,
    baseline_profile: str,
    baseline_px4_commit: str,
    baseline_gazebo_version: str,
    simulator_name: str,
    sitl_verification: dict[str, str],
    positions: list[dict[str, float]],
    started_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "demo-point-rtl-evidence.v0.1",
        "mission_type": MISSION_TYPE,
        "started_at": (started_at or datetime.now(UTC)).isoformat(),
        "overall_status": "passed" if outcome == "completed" else "failed",
        "mavlink_endpoint": redact_loopback_endpoint(endpoint),
        "configured_point_m": {
            "north_m": config.north_m,
            "east_m": config.east_m,
            "altitude_m": config.waypoint_altitude_m,
        },
        "phase_transitions": [
            {"phase": event.phase.value, "timestamp": event.timestamp.isoformat()}
            for event in execution.events
        ],
        "position_samples_hz": 1.0,
        "positions": positions,
        "px4_version": px4_version,
        "simulator": {"name": simulator_name, "gazebo_version": baseline_gazebo_version},
        "sitl_verification": sitl_verification,
        "baseline": {
            "profile": baseline_profile,
            "px4_commit": baseline_px4_commit,
            "gazebo_sim_version": baseline_gazebo_version,
        },
    }


def load_demo_plan_config(plan_path: Path) -> DemoPointRtlConfig:
    import json

    document = json.loads(plan_path.read_text(encoding="utf-8"))
    return DemoPointRtlConfig(
        takeoff_altitude_m=float(document["takeoff_altitude_m"]),
        north_m=float(document["north_m"]),
        east_m=float(document["east_m"]),
        waypoint_altitude_m=float(document["waypoint_altitude_m"]),
        hover_duration_s=float(document.get("hover_duration_s", 0.0)),
        waypoint_tolerance_m=float(document.get("waypoint_tolerance_m", 2.0)),
        waypoint_timeout_s=float(document.get("waypoint_timeout_s", 90.0)),
        landing_timeout_s=float(document.get("landing_timeout_s", 120.0)),
        home_tolerance_m=float(document.get("home_tolerance_m", 6.0)),
    )
