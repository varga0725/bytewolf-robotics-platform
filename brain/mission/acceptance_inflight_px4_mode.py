"""Decode PX4 packed HEARTBEAT custom_mode (v1.17 SITL acceptance)."""

from __future__ import annotations

# PX4 custom main modes (bits 16-23 of custom_mode).
_PX4_MAIN_MANUAL = 1
_PX4_MAIN_POSCTL = 3
_PX4_MAIN_AUTO = 4
_PX4_MAIN_OFFBOARD = 6

# AUTO sub modes (bits 24-31).
_PX4_SUB_AUTO_LOITER = 3
_PX4_SUB_AUTO_RTL = 5
_PX4_SUB_AUTO_LAND = 6


def px4_custom_main_sub(custom_mode: int) -> tuple[int, int]:
    main_mode = (int(custom_mode) >> 16) & 0xFF
    sub_mode = (int(custom_mode) >> 24) & 0xFF
    return main_mode, sub_mode


def px4_flight_mode_from_custom_mode(custom_mode: int) -> str:
    """Map PX4 packed custom_mode to harness flight_mode strings."""
    main_mode, sub_mode = px4_custom_main_sub(custom_mode)
    if main_mode == _PX4_MAIN_AUTO:
        if sub_mode == _PX4_SUB_AUTO_RTL:
            return "return_to_launch"
        if sub_mode == _PX4_SUB_AUTO_LAND:
            return "land"
        if sub_mode == _PX4_SUB_AUTO_LOITER:
            return "loiter"
        return f"auto_{sub_mode}"
    if main_mode == _PX4_MAIN_POSCTL:
        return "position"
    if main_mode == _PX4_MAIN_OFFBOARD:
        return "offboard"
    if main_mode == _PX4_MAIN_MANUAL:
        return "manual"
    return f"px4_main_{main_mode}"


def px4_auto_rtl_custom_mode() -> int:
    return (_PX4_SUB_AUTO_RTL << 24) | (_PX4_MAIN_AUTO << 16)


def px4_auto_land_custom_mode() -> int:
    return (_PX4_SUB_AUTO_LAND << 24) | (_PX4_MAIN_AUTO << 16)


def px4_auto_loiter_custom_mode() -> int:
    return (_PX4_SUB_AUTO_LOITER << 24) | (_PX4_MAIN_AUTO << 16)