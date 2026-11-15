"""In-flight SITL failsafe acceptance: evidence schema and pure scenario evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from simulation.gazebo.fault_injection import PX4_BUILD_DIRECTORY, read_px4_parameter
from simulation.px4.sitl_failsafe_profile import (
    SitlFailsafeProfileError,
    profile_applied_in_run_directory,
    verify_sitl_failsafe_profile,
)

EVIDENCE_VERSION = "acceptance-inflight-sitl.v1"
OPERATION = "acceptance-inflight-sitl"

SCENARIO_DATALINK_LOSS = "datalink-loss"
SCENARIO_GEOFENCE = "geofence"
SCENARIO_KILL_IN_AIR = "kill-in-air"
SCENARIO_LOW_BATTERY = "low-battery"
SCENARIO_RELAY_INFLIGHT = "relay-inflight"

ALL_SCENARIOS: tuple[str, ...] = (
    SCENARIO_DATALINK_LOSS,
    SCENARIO_GEOFENCE,
    SCENARIO_LOW_BATTERY,
    SCENARIO_RELAY_INFLIGHT,
    SCENARIO_KILL_IN_AIR,
)

# SITL profile pins (simulation/px4/params/sitl-failsafe.params)
GF_MAX_HOR_DIST_M = 100.0
GF_MAX_VER_DIST_M = 50.0
COM_DL_LOSS_T_S = 10.0
BAT_LOW_THR = 0.15
BAT_CRIT_THR = 0.07
BAT_EMERGEN_THR = 0.05

GEOFENCE_HOR_TOLERANCE_M = 15.0
GEOFENCE_VER_TOLERANCE_M = 5.0

TAKEOFF_ALTITUDE_M = 20.0
KILL_SCENARIO_ALTITUDE_M = 8.0

Outcome = Literal["PASS", "FAIL"]


@dataclass(frozen=True)
class VehicleTransition:
    """One sampled armed / in_air / flight_mode observation."""

    monotonic_s: float
    armed: bool | None
    in_air: bool | None
    flight_mode: str | None
    battery_percent: float | None = None
    relative_altitude_m: float | None = None
    horizontal_distance_from_home_m: float | None = None


@dataclass
class ProfilePreconditionResult:
    applied: bool
    verified: bool
    px4_run_directory: str | None
    failure_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.applied and self.verified


def check_sitl_failsafe_profile_precondition(
    *,
    px4_run_directory: Path | None,
    px4_build_directory: Path | None = None,
    read_param=read_px4_parameter,
) -> ProfilePreconditionResult:
    """Require status ``applied`` and a full parameter read-back before arming."""
    build = (px4_build_directory or PX4_BUILD_DIRECTORY).resolve()
    run_text = None if px4_run_directory is None else str(px4_run_directory.resolve())
    if px4_run_directory is None:
        return ProfilePreconditionResult(
            applied=False,
            verified=False,
            px4_run_directory=run_text,
            failure_reason="PX4_RUN_DIR / --px4-run-directory is required for profile precondition.",
        )
    if not profile_applied_in_run_directory(px4_run_directory):
        return ProfilePreconditionResult(
            applied=False,
            verified=False,
            px4_run_directory=run_text,
            failure_reason="sitl-failsafe-profile.status does not record applied.",
        )
    try:
        verify_sitl_failsafe_profile(
            px4_build_directory=build,
            read_px4_parameter=lambda name, _directory: read_param(name, build),
        )
    except SitlFailsafeProfileError as error:
        return ProfilePreconditionResult(
            applied=True,
            verified=False,
            px4_run_directory=run_text,
            failure_reason=str(error),
        )
    return ProfilePreconditionResult(
        applied=True,
        verified=True,
        px4_run_directory=run_text,
        failure_reason=None,
    )


def build_evidence_document(
    *,
    scenario: str,
    outcome: Outcome,
    started_at: datetime,
    finished_at: datetime,
    transitions: list[VehicleTransition],
    profile_check: ProfilePreconditionResult,
    timings: dict[str, float],
    failure_reason: str | None = None,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": EVIDENCE_VERSION,
        "scenario": scenario,
        "outcome": outcome,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "finished_at": finished_at.astimezone(UTC).isoformat(),
        "duration_s": max(0.0, (finished_at - started_at).total_seconds()),
        "profile_check": {
            "applied": profile_check.applied,
            "verified": profile_check.verified,
            "px4_run_directory": profile_check.px4_run_directory,
            "failure_reason": profile_check.failure_reason,
        },
        "timings": {key: float(value) for key, value in timings.items()},
        "transitions": [_transition_to_json(item) for item in transitions],
        "failure_reason": failure_reason,
    }
    if notes:
        document["notes"] = list(notes)
    if extra:
        document.update(extra)
    return document


def validate_evidence_document(document: object) -> None:
    if not isinstance(document, dict):
        raise ValueError("Evidence must be a JSON object.")
    for key in ("version", "scenario", "outcome", "profile_check", "transitions", "timings"):
        if key not in document:
            raise ValueError(f"Evidence missing required field '{key}'.")
    if document["version"] != EVIDENCE_VERSION:
        raise ValueError(f"Unexpected evidence version {document['version']!r}.")
    if document["outcome"] not in {"PASS", "FAIL"}:
        raise ValueError("outcome must be PASS or FAIL.")
    if not isinstance(document["transitions"], list):
        raise ValueError("transitions must be a list.")
    for index, item in enumerate(document["transitions"]):
        if not isinstance(item, dict) or "monotonic_s" not in item:
            raise ValueError(f"transitions[{index}] is invalid.")


def _transition_to_json(transition: VehicleTransition) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "monotonic_s": transition.monotonic_s,
        "armed": transition.armed,
        "in_air": transition.in_air,
        "flight_mode": transition.flight_mode,
    }
    if transition.battery_percent is not None:
        payload["battery_percent"] = transition.battery_percent
    if transition.relative_altitude_m is not None:
        payload["relative_altitude_m"] = transition.relative_altitude_m
    if transition.horizontal_distance_from_home_m is not None:
        payload["horizontal_distance_from_home_m"] = transition.horizontal_distance_from_home_m
    return payload


def _mode_name(flight_mode: str | None) -> str:
    return (flight_mode or "").strip().lower()


def _saw_rtl(transitions: list[VehicleTransition], after_s: float = 0.0) -> bool:
    for item in transitions:
        if item.monotonic_s < after_s:
            continue
        mode = _mode_name(item.flight_mode)
        if "return" in mode or mode in {"rtl", "auto_rtl", "auto/rtl"}:
            return True
    return False


def _saw_landed_disarmed(transitions: list[VehicleTransition], after_s: float = 0.0) -> bool:
    saw_not_in_air = False
    for item in transitions:
        if item.monotonic_s < after_s:
            continue
        if item.in_air is False:
            saw_not_in_air = True
        if saw_not_in_air and item.armed is False:
            return True
    return False


def evaluate_datalink_loss_machine(
    transitions: list[VehicleTransition],
    *,
    heartbeat_stopped_at_s: float | None,
    com_dl_loss_t_s: float = COM_DL_LOSS_T_S,
    min_takeoff_altitude_m: float = TAKEOFF_ALTITUDE_M - 2.0,
) -> tuple[bool, str | None]:
    if heartbeat_stopped_at_s is None:
        return False, "GCS heartbeat stop time was not recorded."
    max_alt = max(
        (t.relative_altitude_m for t in transitions if t.relative_altitude_m is not None),
        default=0.0,
    )
    if max_alt < min_takeoff_altitude_m:
        return False, f"Takeoff altitude not reached (max {max_alt:.1f} m)."
    rtl_after = _saw_rtl(transitions, after_s=heartbeat_stopped_at_s + com_dl_loss_t_s - 0.5)
    if not rtl_after:
        return False, "RTL flight mode not observed after COM_DL_LOSS_T."
    if not _saw_landed_disarmed(transitions, after_s=heartbeat_stopped_at_s):
        return False, "Vehicle did not land and disarm after datalink-loss RTL."
    return True, None


def evaluate_geofence_machine(
    transitions: list[VehicleTransition],
    *,
    gf_max_hor_m: float = GF_MAX_HOR_DIST_M,
    gf_max_ver_m: float = GF_MAX_VER_DIST_M,
    hor_tolerance_m: float = GEOFENCE_HOR_TOLERANCE_M,
    ver_tolerance_m: float = GEOFENCE_VER_TOLERANCE_M,
) -> tuple[bool, str | None]:
    if not _saw_rtl(transitions):
        return False, "Geofence breach did not trigger RTL flight mode."
    peak_hor = max(
        (t.horizontal_distance_from_home_m for t in transitions if t.horizontal_distance_from_home_m is not None),
        default=0.0,
    )
    peak_ver = max(
        (t.relative_altitude_m for t in transitions if t.relative_altitude_m is not None),
        default=0.0,
    )
    if peak_hor > gf_max_hor_m + hor_tolerance_m:
        return (
            False,
            f"Horizontal distance {peak_hor:.1f} m exceeded fence {gf_max_hor_m} m + {hor_tolerance_m} m tolerance.",
        )
    if peak_ver > gf_max_ver_m + ver_tolerance_m:
        return (
            False,
            f"Relative altitude {peak_ver:.1f} m exceeded fence {gf_max_ver_m} m + {ver_tolerance_m} m tolerance.",
        )
    if not _saw_landed_disarmed(transitions):
        return False, "Vehicle did not land and disarm after geofence RTL."
    return True, None


def evaluate_kill_in_air_machine(
    *,
    disarm_in_air_rejected: bool,
    kill_to_disarm_s: float | None,
    kill_disarm_deadline_s: float = 1.0,
) -> tuple[bool, str | None]:
    if not disarm_in_air_rejected:
        return False, "Normal in-air disarm was not rejected by PX4."
    if kill_to_disarm_s is None:
        return False, "Emergency kill did not produce a disarmed state."
    if kill_to_disarm_s > kill_disarm_deadline_s:
        return False, f"Kill to disarm took {kill_to_disarm_s:.3f} s (limit {kill_disarm_deadline_s} s)."
    return True, None


def evaluate_low_battery_machine(
    transitions: list[VehicleTransition],
    *,
    low_thr: float = BAT_LOW_THR,
    crit_thr: float = BAT_CRIT_THR,
    emergen_thr: float = BAT_EMERGEN_THR,
) -> tuple[bool, str | None]:
    percents = [t.battery_percent for t in transitions if t.battery_percent is not None]
    if not percents:
        return False, "No battery telemetry samples recorded."
    min_pct = min(percents)
    saw_low = any(p <= low_thr * 100.0 + 0.5 for p in percents)
    saw_crit_rtl = _saw_rtl(transitions) and any(p <= crit_thr * 100.0 + 0.5 for p in percents)
    saw_land = _saw_landed_disarmed(transitions)
    if not saw_low:
        return False, f"Low battery warning not seen (min {min_pct:.1f} %)."
    if not saw_crit_rtl:
        return False, "RTL at critical battery not observed."
    if min_pct > emergen_thr * 100.0 + 1.0 and not saw_land:
        return False, "Emergency land / disarm not observed."
    if not saw_land:
        return False, "Vehicle did not land and disarm after low-battery failsafe."
    return True, None


@dataclass
class RelaySample:
    monotonic_s: float
    captured_at_age_s: float
    in_air: bool | None


def evaluate_relay_inflight_machine(
    samples: list[RelaySample],
    *,
    max_age_s: float = 1.0,
    relay_loopback_udp_only: bool = True,
) -> tuple[bool, str | None]:
    if not relay_loopback_udp_only:
        return False, "Relay exposed a non-loopback or TCP listener."
    if not samples:
        return False, "No relay telemetry samples recorded."
    worst = max(sample.captured_at_age_s for sample in samples)
    if worst >= max_age_s:
        return False, f"Relay captured_at age peaked at {worst:.3f} s (limit {max_age_s} s)."
    in_air_values = [sample.in_air for sample in samples if sample.in_air is not None]
    if True not in in_air_values or False not in in_air_values:
        return False, "in_air false→true→false pattern not visible in relay snapshot."
    # Require false before true and true before final false.
    saw_false = False
    saw_true = False
    saw_final_false = False
    for value in in_air_values:
        if value is False and not saw_true:
            saw_false = True
        if value is True and saw_false:
            saw_true = True
        if value is False and saw_true:
            saw_final_false = True
    if not (saw_false and saw_true and saw_final_false):
        return False, "in_air did not transition false→true→false in order."
    return True, None


def profile_notes_for_scenario(scenario: str) -> list[str]:
    notes = [
        "Harness connects only to loopback UDP SITL (127.0.0.1).",
        "Kill / emergency disarm (param2=21196) is validated in simulation only; "
        "do not use as a real-aircraft procedure.",
    ]
    if scenario == SCENARIO_GEOFENCE:
        notes.append(
            f"Geofence horizontal/vertical peaks allow documented SITL tolerances "
            f"+{GEOFENCE_HOR_TOLERANCE_M:g} m / +{GEOFENCE_VER_TOLERANCE_M:g} m beyond "
            f"GF_MAX_HOR_DIST / GF_MAX_VER_DIST."
        )
    return notes