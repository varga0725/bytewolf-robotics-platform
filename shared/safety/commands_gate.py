"""Operator command surface quarantine (distinct from mission execution)."""

from __future__ import annotations

import os
from typing import Any, Literal

_ENVIRONMENT_VARIABLE = "BYTEWOLF_COMMANDS_ENABLED"
_ControlRoomMode = Literal["ground", "sitl_console"]


class CommandsRouteUnavailableError(RuntimeError):
    """Raised when a command route is disabled and must appear unregistered."""


def commands_enabled(environment: os._Environ[str] | None = None) -> bool:
    """Only an explicit ``true`` value enables command routes."""
    values = os.environ if environment is None else environment
    return values.get(_ENVIRONMENT_VARIABLE, "false").strip().lower() == "true"


def control_room_mode(environment: os._Environ[str] | None = None) -> _ControlRoomMode:
    return "sitl_console" if commands_enabled(environment) else "ground"


def require_commands_enabled() -> None:
    if not commands_enabled():
        raise CommandsRouteUnavailableError()


def capabilities_document(
    *,
    target_class: str | None = None,
    environment: os._Environ[str] | None = None,
) -> dict[str, Any]:
    """Read-only operator surface contract for the dashboard."""
    enabled = commands_enabled(environment)
    document: dict[str, Any] = {
        "mode": control_room_mode(environment),
        "commands_enabled": enabled,
    }
    if target_class is not None:
        document["target_class"] = target_class
    return document