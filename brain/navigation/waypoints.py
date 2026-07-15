"""Conversion of bounded local waypoints into MAVSDK global coordinates."""

from dataclasses import dataclass
from math import cos, radians

from brain.mission.commands import WaypointCommand

_EARTH_RADIUS_M = 6_371_000.0
_DEGREES_PER_RADIAN = 57.29577951308232


@dataclass(frozen=True)
class GlobalPosition:
    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float


def relative_waypoint_to_global(
    origin: GlobalPosition,
    command: WaypointCommand,
    current_relative_altitude_m: float,
) -> GlobalPosition:
    """Translate a north/east target into the global frame MAVSDK requires."""
    latitude_delta = command.north_m / _EARTH_RADIUS_M * _DEGREES_PER_RADIAN
    longitude_delta = (
        command.east_m / (_EARTH_RADIUS_M * cos(radians(origin.latitude_deg)))
        * _DEGREES_PER_RADIAN
    )
    altitude_delta = command.target_altitude_m - current_relative_altitude_m
    return GlobalPosition(
        latitude_deg=origin.latitude_deg + latitude_delta,
        longitude_deg=origin.longitude_deg + longitude_delta,
        absolute_altitude_m=origin.absolute_altitude_m + altitude_delta,
    )
