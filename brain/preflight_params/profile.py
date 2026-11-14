"""Operator-tunable check profile (no vehicle writes)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreflightCheckProfile:
    """Adjustable acceptance rules beyond the fixed RC-less safety baseline."""

    accept_nav_dll_land: bool = False
    """When True, ``NAV_DLL_ACT == 3`` (Land) is treated as PASS instead of FAIL."""