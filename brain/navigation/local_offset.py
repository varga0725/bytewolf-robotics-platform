"""Local north/east offsets from a WGS84 origin (shared with waypoint conversion)."""

from __future__ import annotations

from math import cos, radians

from brain.navigation.waypoints import GlobalPosition, _DEGREES_PER_RADIAN, _EARTH_RADIUS_M


def global_position_to_local_offset(
    origin: GlobalPosition,
    point: GlobalPosition,
) -> tuple[float, float]:
    """Return (north_m, east_m) from origin to point using the local tangent model."""
    north_m = (point.latitude_deg - origin.latitude_deg) / _DEGREES_PER_RADIAN * _EARTH_RADIUS_M
    east_m = (
        (point.longitude_deg - origin.longitude_deg)
        / _DEGREES_PER_RADIAN
        * _EARTH_RADIUS_M
        * cos(radians(origin.latitude_deg))
    )
    return north_m, east_m