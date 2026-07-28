"""Acceptance math for the 30 km/h autonomy target."""

import unittest

from brain.control.high_speed_envelope import (
    HighSpeedEnvelopeError,
    required_sensor_range_m,
    validate_high_speed_envelope,
)


class HighSpeedEnvelopeTests(unittest.TestCase):
    def test_30_kmh_requires_more_than_the_current_30_m_lidar_range(self) -> None:
        required = required_sensor_range_m(
            speed_m_s=30.0 / 3.6,
            reaction_latency_s=0.5,
            braking_deceleration_m_s2=1.0,
            standing_clearance_m=2.0,
        )

        self.assertAlmostEqual(required, 40.889, places=3)
        with self.assertRaisesRegex(HighSpeedEnvelopeError, "requires"):
            validate_high_speed_envelope(
                speed_m_s=30.0 / 3.6,
                sensor_max_range_m=30.0,
                reaction_latency_s=0.5,
                braking_deceleration_m_s2=1.0,
                standing_clearance_m=2.0,
            )

    def test_30_kmh_is_allowed_only_when_the_sensor_horizon_covers_the_stop(self) -> None:
        required = validate_high_speed_envelope(
            speed_m_s=30.0 / 3.6,
            sensor_max_range_m=42.0,
            reaction_latency_s=0.5,
            braking_deceleration_m_s2=1.0,
            standing_clearance_m=2.0,
        )

        self.assertGreater(required, 40.8)
