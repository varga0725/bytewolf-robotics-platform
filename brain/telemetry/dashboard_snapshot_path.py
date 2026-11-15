"""Default on-disk path for the live dashboard telemetry snapshot."""

from __future__ import annotations

import os
from pathlib import Path

_ENV_SNAPSHOT_FILE = "BYTEWOLF_DASHBOARD_TELEMETRY_FILE"
_DEFAULT_RELATIVE = Path("var/dashboard/live-telemetry.json")


def default_dashboard_snapshot_path() -> Path:
    """Runtime snapshot path; never a git-tracked fixtures directory."""
    override = os.environ.get(_ENV_SNAPSHOT_FILE, "").strip()
    if override:
        return Path(override)
    return _DEFAULT_RELATIVE


def resolve_dashboard_snapshot_path(path: Path | None) -> Path:
    """CLI/API default when ``--snapshot-file`` / ``--telemetry-file`` is omitted."""
    return default_dashboard_snapshot_path() if path is None else path


__all__ = ["default_dashboard_snapshot_path", "resolve_dashboard_snapshot_path"]