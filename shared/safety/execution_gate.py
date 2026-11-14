"""Quarantine switch for every path that can start a SITL mission."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


_ENVIRONMENT_VARIABLE = "BYTEWOLF_MISSION_EXECUTION_ENABLED"
_TRUTHY = frozenset({"1", "true", "yes"})


@dataclass(frozen=True)
class MissionExecutionRefusal:
    """Typed refusal when mission execution remains quarantined by default."""

    code: str
    message: str

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class MissionExecutionQuarantinedError(RuntimeError):
    """Raised before any MAVSDK client or mission subprocess is started."""

    def __init__(self, refusal: MissionExecutionRefusal | None = None) -> None:
        self.refusal = refusal or default_execution_refusal()
        super().__init__(self.refusal.message)


def mission_execution_enabled(environment: os._Environ[str] | None = None) -> bool:
    values = os.environ if environment is None else environment
    raw = values.get(_ENVIRONMENT_VARIABLE, "false").strip().lower()
    return raw in _TRUTHY


def default_execution_refusal() -> MissionExecutionRefusal:
    return MissionExecutionRefusal(
        code="mission_execution_quarantined",
        message=(
            "SITL mission execution is quarantined until a stable takeoff acceptance "
            "test is closed. Set BYTEWOLF_MISSION_EXECUTION_ENABLED only after that gate."
        ),
    )


def require_mission_execution_enabled(environment: os._Environ[str] | None = None) -> None:
    if not mission_execution_enabled(environment):
        raise MissionExecutionQuarantinedError(default_execution_refusal())


def execution_refusal_payload() -> dict[str, Any]:
    return default_execution_refusal().as_detail()
