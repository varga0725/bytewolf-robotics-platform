"""Declarative PX4 parameter rules for RC-less Holybro X500 V2 preflight review."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from brain.preflight_params.profile import PreflightCheckProfile

RuleGroup = Literal["core", "extra"]
CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class ParamRule:
    """One row in the preflight rule table."""

    param: str
    expected: str
    group: RuleGroup
    rationale: str
    check: Callable[[float | None, PreflightCheckProfile], tuple[CheckStatus, str]]


def _missing(_value: float | None, _profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    return "FAIL", "not readable"


def _nav_dll_act(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    as_int = int(round(value))
    if as_int == 0:
        return "FAIL", "NAV_DLL_ACT must not be Disabled (0) for RC-less SiK datalink"
    if as_int == 2:
        return "PASS", "Return mode (recommended for datalink loss)"
    if as_int == 3 and profile.accept_nav_dll_land:
        return "PASS", "Land mode (accepted via --accept-nav-dll-land profile)"
    if as_int == 3:
        return (
            "FAIL",
            "Land (3) is only accepted when --accept-nav-dll-land is set; otherwise use Return (2)",
        )
    return "FAIL", f"unexpected NAV_DLL_ACT={as_int}; want 2 (Return) or configured Land (3)"


def _com_dl_loss_t(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    if value <= 0.0:
        return "FAIL", "COM_DL_LOSS_T must be > 0 s"
    if value > 10.0:
        return "FAIL", f"COM_DL_LOSS_T={value:g} s exceeds 10 s maximum"
    return "PASS", f"within 0 < t <= 10 s ({value:g} s)"


def _exact_int(expected: int, label: str) -> Callable[[float | None, PreflightCheckProfile], tuple[CheckStatus, str]]:
    def _check(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
        if value is None:
            return _missing(value, profile)
        as_int = int(round(value))
        if as_int == expected:
            return "PASS", label
        return "FAIL", f"expected {expected}, got {as_int}"

    return _check


def _gf_action(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    as_int = int(round(value))
    if as_int == 0:
        return "FAIL", "GF_ACTION must not be None (0); geofence failsafe disabled"
    return "PASS", f"geofence action enabled ({as_int})"


def _positive_distance(param_label: str) -> Callable[[float | None, PreflightCheckProfile], tuple[CheckStatus, str]]:
    def _check(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
        if value is None:
            return _missing(value, profile)
        if value <= 0.0:
            return "FAIL", f"{param_label} must be > 0 m (0 means geofence disabled)"
        return "PASS", f"{value:g} m"

    return _check


def _com_arm_wo_gps(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    as_int = int(round(value))
    if as_int == 0:
        return "PASS", "Deny arming without GPS (recommended first flight)"
    return "WARN", f"COM_ARM_WO_GPS={as_int}; recommend 0 (Deny) for GPS-dependent RTL"


def _nav_rcl_act(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    return "WARN", f"informational: NAV_RCL_ACT={int(round(value))}"


def _com_rc_loss_t(value: float | None, profile: PreflightCheckProfile) -> tuple[CheckStatus, str]:
    if value is None:
        return _missing(value, profile)
    return "WARN", f"informational: COM_RC_LOSS_T={value:g} s"


RULES: tuple[ParamRule, ...] = (
    ParamRule(
        "NAV_DLL_ACT",
        "2 (Return), or 3 (Land) with profile",
        "core",
        "SiK carries both GCS and joystick; datalink loss must not stay Disabled.",
        _nav_dll_act,
    ),
    ParamRule(
        "COM_DL_LOSS_T",
        "> 0 and <= 10 s",
        "core",
        "GCS datalink-loss timeout before failsafe.",
        _com_dl_loss_t,
    ),
    ParamRule(
        "COM_RC_IN_MODE",
        "1 (MAVLink / joystick only)",
        "core",
        "RC absent; MAVLink-only manual control without RC pre-checks.",
        _exact_int(1, "MAVLink-only manual input"),
    ),
    ParamRule(
        "COM_LOW_BAT_ACT",
        "3 (Warning + RTL/Land)",
        "core",
        "Low battery must command RTL/Land, not warning-only.",
        _exact_int(3, "Return on critical, Land on emergency (v1.17)"),
    ),
    ParamRule(
        "GF_ACTION",
        "!= 0",
        "core",
        "Geofence breach must trigger a failsafe action.",
        _gf_action,
    ),
    ParamRule(
        "GF_MAX_HOR_DIST",
        "> 0 m",
        "core",
        "Horizontal geofence cylinder must be enabled.",
        _positive_distance("GF_MAX_HOR_DIST"),
    ),
    ParamRule(
        "GF_MAX_VER_DIST",
        "> 0 m",
        "core",
        "Vertical geofence limit must be enabled.",
        _positive_distance("GF_MAX_VER_DIST"),
    ),
    ParamRule(
        "COM_ARM_WO_GPS",
        "0 (Deny)",
        "extra",
        "Beyond core list: block arming without GPS for RTL-capable first flights.",
        _com_arm_wo_gps,
    ),
    ParamRule(
        "NAV_RCL_ACT",
        "(reported)",
        "extra",
        "Beyond core list: manual-control-loss action visibility.",
        _nav_rcl_act,
    ),
    ParamRule(
        "COM_RC_LOSS_T",
        "(reported)",
        "extra",
        "Beyond core list: manual-control-loss timeout visibility.",
        _com_rc_loss_t,
    ),
)