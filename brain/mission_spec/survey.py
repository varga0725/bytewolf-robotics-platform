"""Expand one survey request into the waypoints that actually fly it.

A survey is the first mission the operator states as an *area* rather than as a
place. That difference is why it is one step in the spec and many commands in
the compiler: the document a human reviews should say "sweep 30 m around here",
while the thing the gate checks must be every waypoint individually. Hiding the
waypoints from the gate would be the whole point of the gate, gone.

The pattern is a boustrophedon (lawnmower) sweep clipped to a circle: parallel
north-south lines spaced `spacing_m` apart, each traversed in the opposite
direction to the last so the vehicle never flies a long empty return leg.

Every bound here is a refusal, not a clamp. A spacing that would need more
waypoints than the cap does not get silently coarsened — a quietly widened
spacing is a survey with holes in it that still reports success.
"""

from __future__ import annotations

from math import hypot, isfinite, sqrt


# A 2D lidar sweep is only meaningful if consecutive lines overlap what the
# sensor can see; below a metre the flight is mostly turns, above ~15 m the
# sweep stops being a survey and becomes a few transits.
MIN_SPACING_M = 1.0
MAX_SPACING_M = 15.0
MIN_RADIUS_M = 2.0
# The cap is a battery and duration bound as much as a compile bound: at 3 m/s
# with the twin's endurance, a few hundred legs is not a flight anyone lands.
MAX_SURVEY_WAYPOINTS = 60


class SurveyPatternError(ValueError):
    """The requested area cannot be swept within the bounds that make it a survey."""


def survey_waypoints(
    *,
    centre_north_m: float,
    centre_east_m: float,
    radius_m: float,
    spacing_m: float,
) -> tuple[tuple[float, float], ...]:
    """Return the ordered north/east waypoints that sweep one circular area.

    The first waypoint is the one nearest the pattern's entry corner, and the
    last leaves the vehicle at the far edge — the mission's terminal RTL brings
    it home, so the pattern itself never assumes where home is.
    """
    for name, value in (
        ("centre_north_m", centre_north_m),
        ("centre_east_m", centre_east_m),
        ("radius_m", radius_m),
        ("spacing_m", spacing_m),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
            raise SurveyPatternError(f"A survey needs a finite {name}.")
    if radius_m < MIN_RADIUS_M:
        raise SurveyPatternError(f"A survey radius must be at least {MIN_RADIUS_M:g} m.")
    if not MIN_SPACING_M <= spacing_m <= MAX_SPACING_M:
        raise SurveyPatternError(
            f"Survey line spacing must be between {MIN_SPACING_M:g} m and {MAX_SPACING_M:g} m."
        )

    offsets = _line_offsets(radius_m, spacing_m)
    waypoints: list[tuple[float, float]] = []
    for index, east_offset in enumerate(offsets):
        # Half-chord of the circle at this line, so the sweep stays inside the
        # requested area instead of squaring it off into the corners.
        half_chord = sqrt(max(0.0, radius_m**2 - east_offset**2))
        if half_chord <= 0:
            continue
        ends = (-half_chord, half_chord) if index % 2 == 0 else (half_chord, -half_chord)
        for north_offset in ends:
            waypoints.append((centre_north_m + north_offset, centre_east_m + east_offset))

    if not waypoints:
        raise SurveyPatternError("The requested area produced no waypoints.")
    if len(waypoints) > MAX_SURVEY_WAYPOINTS:
        raise SurveyPatternError(
            f"This area needs {len(waypoints)} waypoints at {spacing_m:g} m spacing, "
            f"above the {MAX_SURVEY_WAYPOINTS} limit. Widen the spacing or shrink the radius."
        )
    return tuple(waypoints)


def survey_reach_m(
    *, centre_north_m: float, centre_east_m: float, radius_m: float
) -> float:
    """The farthest the vehicle can get from home while flying this survey.

    The mission radius applies to every waypoint, so an area is only flyable if
    its far edge is inside it — checking only the centre would let a 30 m sweep
    centred at 40 m fly to 70 m.
    """
    return hypot(centre_north_m, centre_east_m) + radius_m


def _line_offsets(radius_m: float, spacing_m: float) -> list[float]:
    """East offsets of the sweep lines, symmetric about the centre."""
    offsets = [0.0]
    step = spacing_m
    while step < radius_m:
        offsets.extend((-step, step))
        step += spacing_m
    return sorted(offsets)
