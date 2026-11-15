"""SITL-only PX4 failsafe parameter profile for v1.17.0 headless simulation."""

from __future__ import annotations

from pathlib import Path

from brain.mission.sitl_target import (
    _PARAM_SIM_GZ_EN,
    _PARAM_SIH_VEHICLE_TYPE,
    _PARAM_SYS_AUTOSTART,
    collect_param_based_sitl_signals,
    sitl_indicators_sufficient,
)
from brain.preflight_params.sources import parse_qgc_params_file
from simulation.gazebo.fault_injection import AppliedParameter, apply_px4_parameters

_PARAMS_FILE = Path(__file__).resolve().parent / "params" / "sitl-failsafe.params"


class SitlFailsafeProfileError(RuntimeError):
    """The profile refused to apply or could not be confirmed on the target."""


def sitl_failsafe_params_file() -> Path:
    return _PARAMS_FILE


def sitl_failsafe_parameter_tuples() -> tuple[tuple[str, float], ...]:
    """Profile values as (name, float) for PX4's parameter client."""
    parsed = parse_qgc_params_file(_PARAMS_FILE)
    return tuple((name, float(entry.display_value)) for name, entry in sorted(parsed.items()))


def refuse_apply_without_sitl_indicators(
    *,
    sys_autostart: int,
    sim_gz_en: int | None,
    sih_vehicle_type: int | None,
) -> list[str]:
    """Raise when PX4 does not present enough independent SITL indicators."""
    matched = collect_param_based_sitl_signals(
        sys_autostart=sys_autostart,
        sim_gz_en=sim_gz_en,
        sih_vehicle_type=sih_vehicle_type,
    )
    if sitl_indicators_sufficient(matched):
        return matched
    raise SitlFailsafeProfileError(
        "Refusing SITL failsafe profile: PX4 did not present enough independent SITL "
        f"indicators (matched {matched or ['none']})."
    )


def apply_sitl_failsafe_profile(
    *,
    px4_build_directory: Path,
    read_px4_parameter,
    apply_parameters=apply_px4_parameters,
) -> tuple[AppliedParameter, ...]:
    """Apply the profile through PX4's local client after the SITL gate passes."""

    sys_autostart = int(read_px4_parameter(_PARAM_SYS_AUTOSTART, px4_build_directory))
    sim_gz_en: int | None = None
    sih_vehicle_type: int | None = None
    try:
        sim_gz_en = int(read_px4_parameter(_PARAM_SIM_GZ_EN, px4_build_directory))
    except Exception:
        sim_gz_en = None
    try:
        sih_vehicle_type = int(read_px4_parameter(_PARAM_SIH_VEHICLE_TYPE, px4_build_directory))
    except Exception:
        sih_vehicle_type = None

    refuse_apply_without_sitl_indicators(
        sys_autostart=sys_autostart,
        sim_gz_en=sim_gz_en,
        sih_vehicle_type=sih_vehicle_type,
    )
    return apply_parameters(
        sitl_failsafe_parameter_tuples(),
        px4_build_directory=px4_build_directory,
    )