"""Resolve reviewed plan files for approval hashing and validation."""

from __future__ import annotations

from pathlib import Path

from apps.gateway.telegram_mission_gateway import _DEFAULT_PLAN_DIRECTORY, _PLAN_NAME


def resolve_plan_path(plan_id: str, plan_directory: Path | None = None) -> Path:
    directory = _DEFAULT_PLAN_DIRECTORY if plan_directory is None else plan_directory
    if not _PLAN_NAME.fullmatch(plan_id):
        raise ValueError("Invalid plan identifier.")
    path = directory / plan_id
    if not path.is_file():
        raise FileNotFoundError(f"Reviewed plan file is missing: '{path}'.")
    return path