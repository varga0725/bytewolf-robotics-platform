"""Shared pre-arm SITL identity gate for mission CLIs."""

from __future__ import annotations

from typing import Any

from brain.mission.sitl_target import require_px4_sitl_indication

_DEFAULT_SITL_VERIFY_TIMEOUT_S = 10.0


async def require_sitl_before_arm(drone: object, *, timeout_s: float = _DEFAULT_SITL_VERIFY_TIMEOUT_S) -> dict[str, Any]:
    """Refuse to arm unless the connected PX4 target proves SITL identity."""
    return await require_px4_sitl_indication(drone, timeout_s=timeout_s)