"""Fail-closed sensor-horizon gate for high-speed autonomy scenarios.

This is not a vehicle dynamics identification model.  It converts the shield's
declared latency, braking and standing-clearance assumptions into the minimum
distance a forward sensor must observe before a speed may be admitted.
"""

from __future__ import annotations

from math import isfinite


class HighSpeedEnvelopeError(ValueError):
    """A requested speed has no covered stop horizon."""


def required_sensor_range_m(
    *, speed_m_s: float, reaction_latency_s: float,
    braking_deceleration_m_s2: float, standing_clearance_m: float,
) -> float:
    """Return reaction distance + braking distance + standing clearance."""
    _positive(speed_m_s, "speed_m_s")
    _positive(reaction_latency_s, "reaction_latency_s")
    _positive(braking_deceleration_m_s2, "braking_deceleration_m_s2")
    _positive(standing_clearance_m, "standing_clearance_m")
    return (
        speed_m_s * reaction_latency_s
        + speed_m_s * speed_m_s / (2.0 * braking_deceleration_m_s2)
        + standing_clearance_m
    )


def validate_high_speed_envelope(
    *, speed_m_s: float, sensor_max_range_m: float,
    reaction_latency_s: float, braking_deceleration_m_s2: float,
    standing_clearance_m: float,
) -> float:
    """Refuse a speed whose stopping horizon exceeds trusted sensor range."""
    _positive(sensor_max_range_m, "sensor_max_range_m")
    required_m = required_sensor_range_m(
        speed_m_s=speed_m_s,
        reaction_latency_s=reaction_latency_s,
        braking_deceleration_m_s2=braking_deceleration_m_s2,
        standing_clearance_m=standing_clearance_m,
    )
    if required_m > sensor_max_range_m:
        raise HighSpeedEnvelopeError(
            f"{speed_m_s * 3.6:.1f} km/h requires {required_m:.1f} m sensor range; "
            f"only {sensor_max_range_m:.1f} m is covered."
        )
    return required_m


def _positive(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0.0:
        raise HighSpeedEnvelopeError(f"{name} must be positive and finite.")


__all__ = [
    "HighSpeedEnvelopeError",
    "required_sensor_range_m",
    "validate_high_speed_envelope",
]

