"""Shared live dashboard snapshot path resolution for CLIs."""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

from brain.telemetry.dashboard_snapshot_path import default_dashboard_snapshot_path, resolve_dashboard_snapshot_path


def add_dashboard_snapshot_argument(
    parser: ArgumentParser,
    *,
    option: str = "--dashboard-snapshot",
    dest: str = "dashboard_snapshot",
    help_text: str = "Read-only telemetry JSON snapshot, updated during this mission.",
) -> None:
    parser.add_argument(option, type=Path, default=None, dest=dest, help=help_text)


def add_telemetry_file_argument(
    parser: ArgumentParser,
    *,
    option: str = "--telemetry-file",
    dest: str = "telemetry_file",
    help_text: str = "Live dashboard telemetry JSON file.",
) -> None:
    parser.add_argument(option, type=Path, default=None, dest=dest, help=help_text)


def effective_dashboard_snapshot_path(value: Path | None) -> Path:
    return resolve_dashboard_snapshot_path(value)


def effective_default_path() -> Path:
    return default_dashboard_snapshot_path()


__all__ = [
    "add_dashboard_snapshot_argument",
    "add_telemetry_file_argument",
    "effective_dashboard_snapshot_path",
    "effective_default_path",
]