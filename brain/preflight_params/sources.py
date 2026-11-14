"""Read-only parameter sources (file export or MAVLink)."""

from __future__ import annotations

import re
from pathlib import Path

from brain.preflight_params.mavlink import MavlinkReadError, fetch_params_over_mavlink

# QGC / PX4 .params lines: optional vehicle/component columns, then name and value.
_PARAM_LINE = re.compile(
    r"^\s*(?:\d+\s+\d+\s+)?(?P<name>[A-Z0-9_]+)\s+(?P<value>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?:#.*)?$"
)


def parse_qgc_params_file(path: Path) -> dict[str, float]:
    """Parse a QGroundControl parameter export without contacting the vehicle."""

    params: dict[str, float] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PARAM_LINE.match(line)
        if not match:
            raise MavlinkReadError(f"Cannot parse parameter line {line_number} in {path}: {raw_line!r}")
        params[match.group("name")] = float(match.group("value"))
    if not params:
        raise MavlinkReadError(f"No parameters found in {path}")
    return params


def load_params_from_connection(connection: str, *, timeout_s: float) -> dict[str, float]:
    """Download the PX4 parameter set over MAVLink (read-only)."""

    return fetch_params_over_mavlink(connection, timeout_s=timeout_s)