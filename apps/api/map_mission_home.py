"""PX4 home anchoring for map-picked mission proposals (server-authoritative N/E)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

from apps.api.telemetry import TelemetryFormatError, TelemetrySnapshot, load_telemetry_snapshot
from brain.mission.demo_errors import DemoWaypointMissionError
from brain.navigation.waypoints import GlobalPosition, global_position_to_local_offset, horizontal_distance_m

HOME_MISMATCH_THRESHOLD_M = 2.0
# Same budget the dashboard uses to call telemetry stale; an older snapshot means a dead relay.
HOME_SNAPSHOT_MAX_AGE_S = 10.0


@dataclass(frozen=True)
class MapHomeReference:
    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float
    captured_at: str

    def as_global(self) -> GlobalPosition:
        return GlobalPosition(
            latitude_deg=self.latitude_deg,
            longitude_deg=self.longitude_deg,
            absolute_altitude_m=self.absolute_altitude_m,
        )


def parse_map_home_reference(document: dict[str, object]) -> MapHomeReference:
    home = document.get("home")
    if not isinstance(home, dict):
        raise DemoWaypointMissionError("A PX4 home reference is required for map waypoints.")
    latitude = home.get("latitude_deg")
    longitude = home.get("longitude_deg")
    altitude = home.get("absolute_altitude_m", 0.0)
    captured_at = home.get("captured_at")
    if not all(isinstance(value, (int, float)) and isfinite(float(value)) for value in (latitude, longitude)):
        raise DemoWaypointMissionError("home latitude_deg and longitude_deg must be finite numbers.")
    if not isinstance(altitude, (int, float)) or not isfinite(float(altitude)):
        raise DemoWaypointMissionError("home absolute_altitude_m must be finite when present.")
    if not isinstance(captured_at, str) or not captured_at.strip():
        raise DemoWaypointMissionError("home captured_at must be a non-empty ISO timestamp.")
    return MapHomeReference(
        latitude_deg=float(latitude),
        longitude_deg=float(longitude),
        absolute_altitude_m=float(altitude),
        captured_at=captured_at.strip(),
    )


def load_snapshot_home(telemetry_path: Path, *, now: datetime | None = None) -> MapHomeReference:
    try:
        snapshot = load_telemetry_snapshot(telemetry_path)
    except (TelemetryFormatError, OSError) as error:
        raise DemoWaypointMissionError("Telemetry snapshot is unavailable; PX4 home is unknown.") from error
    if snapshot.captured_at is None:
        raise DemoWaypointMissionError("Telemetry snapshot has no captured_at; PX4 home freshness is unknown.")
    captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
    age_s = ((now or datetime.now(UTC)) - captured).total_seconds()
    # A future timestamp is a broken clock, not freshness.
    if not -HOME_SNAPSHOT_MAX_AGE_S <= age_s <= HOME_SNAPSHOT_MAX_AGE_S:
        raise DemoWaypointMissionError(
            f"Telemetry snapshot is {age_s:.0f} s old (limit {HOME_SNAPSHOT_MAX_AGE_S:g} s); "
            "the relay is not live, so PX4 home cannot be trusted."
        )
    home = snapshot.home
    if home is None:
        raise DemoWaypointMissionError(
            "PX4 HOME_POSITION is not in the telemetry snapshot; map planning is disabled."
        )
    return MapHomeReference(
        latitude_deg=home.latitude_deg,
        longitude_deg=home.longitude_deg,
        absolute_altitude_m=home.absolute_altitude_m,
        captured_at=home.captured_at,
    )


def assert_home_matches(
    proposed: MapHomeReference,
    *,
    authority: MapHomeReference,
    threshold_m: float = HOME_MISMATCH_THRESHOLD_M,
) -> None:
    separation_m = horizontal_distance_m(proposed.as_global(), authority.as_global())
    if separation_m > threshold_m:
        raise DemoWaypointMissionError(
            f"Proposal home differs from PX4 home by {separation_m:.2f} m "
            f"(limit {threshold_m:g} m). Refresh telemetry and replan."
        )


def waypoint_commands_from_geographic(
    *,
    home: MapHomeReference,
    waypoints: tuple[dict[str, float], ...],
    default_altitude_m: float,
) -> list[tuple[float, float, float]]:
    origin = home.as_global()
    commands: list[tuple[float, float, float]] = []
    for item in waypoints:
        latitude = item["latitude_deg"]
        longitude = item["longitude_deg"]
        altitude = item.get("relative_altitude_m", default_altitude_m)
        point = GlobalPosition(latitude_deg=latitude, longitude_deg=longitude, absolute_altitude_m=0.0)
        north_m, east_m = global_position_to_local_offset(origin, point)
        commands.append((north_m, east_m, altitude))
    return commands


def home_reference_from_plan_document(document: dict[str, Any]) -> MapHomeReference | None:
    home = document.get("map_home")
    if not isinstance(home, dict):
        return None
    try:
        return parse_map_home_reference({"home": home})
    except DemoWaypointMissionError:
        return None


def _plan_has_map_origin_waypoints(document: dict[str, Any]) -> bool:
    geo_waypoints = document.get("geo_waypoints")
    return isinstance(geo_waypoints, list) and len(geo_waypoints) > 0


def validate_plan_home_at_execution(
    plan_path: Path, telemetry_path: Path, *, now: datetime | None = None
) -> None:
    import json

    document = json.loads(plan_path.read_text(encoding="utf-8"))
    stored = home_reference_from_plan_document(document)
    map_origin = _plan_has_map_origin_waypoints(document)
    if stored is None:
        if map_origin:
            raise DemoWaypointMissionError(
                "Map-origin plan is missing map_home; execution refused."
            )
        return
    current = load_snapshot_home(telemetry_path, now=now)
    assert_home_matches(stored, authority=current)