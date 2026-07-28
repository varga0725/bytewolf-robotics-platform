"""Pure local-route planning for the shielded Offboard mission path.

The planner does not fly, read telemetry, or inspect obstacles.  It converts a
launch-relative NED position error into one bounded body-FRD velocity.  The
runtime shield remains the authority that may preserve or stop that nominal
command before the Offboard boundary can deliver it.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, isfinite, radians, sin

from brain.control.contract import Velocity


class OffboardRouteError(ValueError):
    """Raised when route state cannot safely produce a velocity."""


@dataclass(frozen=True)
class RouteVelocityDecision:
    """One immutable nominal route decision."""

    velocity: Velocity
    reached: bool
    horizontal_error_m: float
    altitude_error_m: float


def plan_body_velocity(
    *,
    north_error_m: float,
    east_error_m: float,
    altitude_error_m: float,
    heading_deg: float,
    max_horizontal_speed_m_s: float,
    max_vertical_speed_m_s: float,
    arrival_tolerance_m: float,
    slowdown_radius_m: float,
) -> RouteVelocityDecision:
    """Return a bounded body-FRD command for a launch-relative target.

    ``altitude_error_m`` is positive when the target is above the vehicle.
    Body-FRD ``z_m_s`` is positive down, hence the explicit sign inversion.
    Invalid state is refused rather than converted into a zero that could be
    mistaken for a successfully reached waypoint.
    """
    state = {
        "north_error_m": north_error_m,
        "east_error_m": east_error_m,
        "altitude_error_m": altitude_error_m,
        "heading_deg": heading_deg,
    }
    for name, value in state.items():
        _require_finite(name, value)
    if not -180.0 <= heading_deg <= 180.0:
        raise OffboardRouteError(
            "heading_deg must use the canonical [-180, 180] telemetry range."
        )
    limits = {
        "max_horizontal_speed_m_s": max_horizontal_speed_m_s,
        "max_vertical_speed_m_s": max_vertical_speed_m_s,
        "arrival_tolerance_m": arrival_tolerance_m,
        "slowdown_radius_m": slowdown_radius_m,
    }
    for name, value in limits.items():
        _require_positive(name, value)
    if slowdown_radius_m <= arrival_tolerance_m:
        raise OffboardRouteError(
            "slowdown_radius_m must exceed arrival_tolerance_m."
        )

    horizontal_error_m = hypot(north_error_m, east_error_m)
    if not isfinite(horizontal_error_m):
        raise OffboardRouteError(
            "The derived horizontal route error is not finite."
        )
    # This preserves the existing mission adapter's cylindrical arrival
    # contract: horizontal and altitude tolerances are judged independently.
    reached = (
        horizontal_error_m <= arrival_tolerance_m
        and abs(altitude_error_m) <= arrival_tolerance_m
    )
    if reached:
        return RouteVelocityDecision(
            velocity=Velocity(0.0, 0.0, 0.0, 0.0),
            reached=True,
            horizontal_error_m=horizontal_error_m,
            altitude_error_m=altitude_error_m,
        )

    horizontal_speed = max_horizontal_speed_m_s * min(
        1.0, horizontal_error_m / slowdown_radius_m
    )
    if not isfinite(horizontal_speed):
        raise OffboardRouteError("The derived horizontal speed is not finite.")
    if horizontal_error_m > 0.0:
        north_velocity = north_error_m / horizontal_error_m * horizontal_speed
        east_velocity = east_error_m / horizontal_error_m * horizontal_speed
    else:
        north_velocity = 0.0
        east_velocity = 0.0

    yaw = radians(heading_deg)
    forward_velocity = cos(yaw) * north_velocity + sin(yaw) * east_velocity
    right_velocity = -sin(yaw) * north_velocity + cos(yaw) * east_velocity
    down_velocity = -_clamp(
        altitude_error_m, -max_vertical_speed_m_s, max_vertical_speed_m_s
    )

    # The Offboard boundary applies the vehicle's single translational speed
    # limit to the 3-D magnitude. Preserve that invariant here too, so a valid
    # horizontal and vertical component cannot combine into a rejected command.
    magnitude = hypot(forward_velocity, right_velocity, down_velocity)
    if not isfinite(magnitude):
        raise OffboardRouteError("The derived body-FRD velocity is not finite.")
    if magnitude > max_horizontal_speed_m_s:
        scale = max_horizontal_speed_m_s / magnitude
        forward_velocity *= scale
        right_velocity *= scale
        down_velocity *= scale

    return RouteVelocityDecision(
        velocity=Velocity(
            x_m_s=forward_velocity,
            y_m_s=right_velocity,
            z_m_s=down_velocity,
            yaw_rate_deg_s=0.0,
        ),
        reached=False,
        horizontal_error_m=horizontal_error_m,
        altitude_error_m=altitude_error_m,
    )


def body_cross_track_velocity(
    *,
    path_north_m: float,
    path_east_m: float,
    heading_deg: float,
    lateral_speed_m_s: float,
    right: bool,
) -> Velocity:
    """Map a route-relative lateral bypass into the current ``body_frd`` frame.

    ``right`` is relative to the original mission path in NED, not to the
    vehicle's launch yaw.  This keeps the detour geometrically meaningful when
    PX4 starts the vehicle with an arbitrary heading.
    """
    _require_finite("path_north_m", path_north_m)
    _require_finite("path_east_m", path_east_m)
    _require_finite("heading_deg", heading_deg)
    _require_positive("lateral_speed_m_s", lateral_speed_m_s)
    if not -180.0 <= heading_deg <= 180.0:
        raise OffboardRouteError(
            "heading_deg must use the canonical [-180, 180] telemetry range."
        )
    path_length_m = hypot(path_north_m, path_east_m)
    if not isfinite(path_length_m) or path_length_m <= 0.0:
        raise OffboardRouteError("The mission path direction must be non-zero.")
    direction_north = path_north_m / path_length_m
    direction_east = path_east_m / path_length_m
    sign = 1.0 if right else -1.0
    # NED right-hand normal for the route tangent.
    north_velocity = -sign * direction_east * lateral_speed_m_s
    east_velocity = sign * direction_north * lateral_speed_m_s
    yaw = radians(heading_deg)
    return Velocity(
        x_m_s=cos(yaw) * north_velocity + sin(yaw) * east_velocity,
        y_m_s=-sin(yaw) * north_velocity + cos(yaw) * east_velocity,
        z_m_s=0.0,
        yaw_rate_deg_s=0.0,
    )


def cross_track_error_m(
    *,
    north_error_m: float,
    east_error_m: float,
    path_north_m: float,
    path_east_m: float,
) -> float:
    """Return signed target-error component normal to the original route."""
    for name, value in {
        "north_error_m": north_error_m,
        "east_error_m": east_error_m,
        "path_north_m": path_north_m,
        "path_east_m": path_east_m,
    }.items():
        _require_finite(name, value)
    path_length_m = hypot(path_north_m, path_east_m)
    if not isfinite(path_length_m) or path_length_m <= 0.0:
        raise OffboardRouteError("The mission path direction must be non-zero.")
    # Dot the target error against the route's right-hand normal.
    return (
        east_error_m * path_north_m - north_error_m * path_east_m
    ) / path_length_m


def along_track_error_m(
    *, north_error_m: float, east_error_m: float,
    path_north_m: float, path_east_m: float,
) -> float:
    """Return remaining target error projected onto the frozen path tangent."""
    for name, value in {
        "north_error_m": north_error_m, "east_error_m": east_error_m,
        "path_north_m": path_north_m, "path_east_m": path_east_m,
    }.items():
        _require_finite(name, value)
    length = hypot(path_north_m, path_east_m)
    if not isfinite(length) or length <= 0.0:
        raise OffboardRouteError("The mission path direction must be non-zero.")
    return (north_error_m * path_north_m + east_error_m * path_east_m) / length


def body_along_track_velocity(
    *, path_north_m: float, path_east_m: float, heading_deg: float,
    speed_m_s: float,
) -> Velocity:
    """Map a forward frozen-path velocity into the current body-FRD frame."""
    _require_positive("speed_m_s", speed_m_s)
    _require_finite("path_north_m", path_north_m)
    _require_finite("path_east_m", path_east_m)
    _require_finite("heading_deg", heading_deg)
    if not -180.0 <= heading_deg <= 180.0:
        raise OffboardRouteError("heading_deg must use the canonical [-180, 180] telemetry range.")
    length = hypot(path_north_m, path_east_m)
    if not isfinite(length) or length <= 0.0:
        raise OffboardRouteError("The mission path direction must be non-zero.")
    north_velocity = path_north_m / length * speed_m_s
    east_velocity = path_east_m / length * speed_m_s
    yaw = radians(heading_deg)
    return Velocity(
        x_m_s=cos(yaw) * north_velocity + sin(yaw) * east_velocity,
        y_m_s=-sin(yaw) * north_velocity + cos(yaw) * east_velocity,
        z_m_s=0.0, yaw_rate_deg_s=0.0,
    )


def yaw_alignment_velocity(
    *,
    north_error_m: float,
    east_error_m: float,
    heading_deg: float,
    max_yaw_rate_deg_s: float,
    alignment_gain: float = 1.0,
) -> Velocity:
    """Turn in place toward the NED target without translating into a blind sector."""
    for name, value in {
        "north_error_m": north_error_m,
        "east_error_m": east_error_m,
        "heading_deg": heading_deg,
        "max_yaw_rate_deg_s": max_yaw_rate_deg_s,
        "alignment_gain": alignment_gain,
    }.items():
        _require_finite(name, value)
    if not -180.0 <= heading_deg <= 180.0:
        raise OffboardRouteError("heading_deg must use the canonical [-180, 180] telemetry range.")
    _require_positive("max_yaw_rate_deg_s", max_yaw_rate_deg_s)
    _require_positive("alignment_gain", alignment_gain)
    if hypot(north_error_m, east_error_m) <= 0.0:
        raise OffboardRouteError("Yaw alignment needs a non-zero target error.")
    desired_heading = degrees(atan2(east_error_m, north_error_m))
    heading_error = (desired_heading - heading_deg + 180.0) % 360.0 - 180.0
    yaw_rate = max(
        -max_yaw_rate_deg_s,
        min(max_yaw_rate_deg_s, heading_error * alignment_gain),
    )
    return Velocity(0.0, 0.0, 0.0, yaw_rate)


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise OffboardRouteError(f"{name} must be a finite number.")


def _require_positive(name: str, value: float) -> None:
    _require_finite(name, value)
    if value <= 0.0:
        raise OffboardRouteError(f"{name} must be positive.")


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


__all__ = [
    "OffboardRouteError",
    "RouteVelocityDecision",
    "plan_body_velocity",
]
