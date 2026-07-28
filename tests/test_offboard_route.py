import math
import sys
import unittest
from dataclasses import FrozenInstanceError

from brain.navigation.offboard_route import (
    OffboardRouteError,
    RouteVelocityDecision,
    body_cross_track_velocity,
    cross_track_error_m,
    plan_body_velocity,
)


class OffboardRoutePlannerTests(unittest.TestCase):
    def plan(self, **overrides) -> RouteVelocityDecision:
        values = {
            "north_error_m": 10.0,
            "east_error_m": 0.0,
            "altitude_error_m": 0.0,
            "heading_deg": 0.0,
            "max_horizontal_speed_m_s": 3.0,
            "max_vertical_speed_m_s": 1.0,
            "arrival_tolerance_m": 0.5,
            "slowdown_radius_m": 3.0,
        }
        values.update(overrides)
        return plan_body_velocity(**values)

    def test_cardinal_world_errors_are_rotated_into_body_frd(self) -> None:
        cases = (
            (10.0, 0.0, 0.0, 3.0, 0.0),
            (0.0, 10.0, 0.0, 0.0, 3.0),
            (10.0, 0.0, 90.0, 0.0, -3.0),
            (0.0, 10.0, 90.0, 3.0, 0.0),
            (-10.0, 0.0, 180.0, 3.0, 0.0),
        )
        for north, east, heading, forward, right in cases:
            with self.subTest(north=north, east=east, heading=heading):
                decision = self.plan(
                    north_error_m=north, east_error_m=east, heading_deg=heading
                )
                self.assertAlmostEqual(decision.velocity.x_m_s, forward, places=6)
                self.assertAlmostEqual(decision.velocity.y_m_s, right, places=6)

    def test_horizontal_speed_is_capped_by_the_vehicle_limit(self) -> None:
        decision = self.plan(north_error_m=10.0, east_error_m=10.0)

        self.assertAlmostEqual(
            math.hypot(decision.velocity.x_m_s, decision.velocity.y_m_s), 3.0
        )

    def test_combined_horizontal_and_vertical_speed_respects_the_vehicle_limit(self) -> None:
        decision = self.plan(altitude_error_m=4.0)

        self.assertLessEqual(decision.velocity.speed_m_s, 3.0)
        self.assertGreater(decision.velocity.x_m_s, 0.0)
        self.assertLess(decision.velocity.z_m_s, 0.0)

    def test_speed_slows_proportionally_inside_the_slowdown_radius(self) -> None:
        decision = self.plan(north_error_m=1.5)

        self.assertAlmostEqual(decision.velocity.x_m_s, 1.5)

    def test_arrival_emits_an_exact_zero_velocity(self) -> None:
        decision = self.plan(
            north_error_m=0.2, east_error_m=0.2, altitude_error_m=0.2
        )

        self.assertTrue(decision.reached)
        self.assertEqual(decision.velocity.speed_m_s, 0.0)
        self.assertEqual(decision.velocity.z_m_s, 0.0)

    def test_altitude_error_uses_frd_down_sign_and_is_capped(self) -> None:
        above = self.plan(north_error_m=0.0, altitude_error_m=4.0)
        below = self.plan(north_error_m=0.0, altitude_error_m=-4.0)

        self.assertEqual(above.velocity.z_m_s, -1.0)
        self.assertEqual(below.velocity.z_m_s, 1.0)

    def test_heading_wrap_is_continuous(self) -> None:
        positive = self.plan(heading_deg=180.0)
        negative = self.plan(heading_deg=-180.0)

        self.assertAlmostEqual(positive.velocity.x_m_s, negative.velocity.x_m_s)
        self.assertAlmostEqual(positive.velocity.y_m_s, negative.velocity.y_m_s)

    def test_cross_track_commands_and_progress_are_world_path_relative(self) -> None:
        # A GUI vehicle may launch at any yaw.  A northbound route's right-hand
        # bypass remains eastward in NED, rather than becoming body-right.
        right = body_cross_track_velocity(
            path_north_m=15.0,
            path_east_m=0.0,
            heading_deg=96.552,
            lateral_speed_m_s=0.4,
            right=True,
        )
        self.assertAlmostEqual(right.x_m_s, 0.397, places=3)
        self.assertAlmostEqual(right.y_m_s, -0.046, places=3)
        self.assertAlmostEqual(right.speed_m_s, 0.4, places=6)
        self.assertEqual(
            cross_track_error_m(
                north_error_m=15.0,
                east_error_m=-3.0,
                path_north_m=15.0,
                path_east_m=0.0,
            ),
            -3.0,
        )

    def test_heading_outside_the_telemetry_contract_fails_closed(self) -> None:
        for heading in (-180.001, 180.001, sys.float_info.max):
            with self.subTest(heading=heading):
                with self.assertRaisesRegex(OffboardRouteError, "canonical"):
                    self.plan(heading_deg=heading)

    def test_non_finite_state_fails_closed(self) -> None:
        for field in (
            "north_error_m",
            "east_error_m",
            "altitude_error_m",
            "heading_deg",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(OffboardRouteError, field):
                    self.plan(**{field: math.nan})

    def test_finite_inputs_that_overflow_derived_geometry_fail_closed(self) -> None:
        with self.assertRaisesRegex(OffboardRouteError, "derived horizontal"):
            self.plan(
                north_error_m=sys.float_info.max,
                east_error_m=sys.float_info.max,
            )

    def test_invalid_value_types_and_infinities_fail_closed(self) -> None:
        for field, value in (
            ("north_error_m", math.inf),
            ("east_error_m", -math.inf),
            ("heading_deg", True),
            ("altitude_error_m", "unknown"),
            ("max_horizontal_speed_m_s", math.inf),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(OffboardRouteError, field):
                    self.plan(**{field: value})

    def test_invalid_limits_are_rejected(self) -> None:
        for field, value in (
            ("max_horizontal_speed_m_s", 0.0),
            ("max_vertical_speed_m_s", -1.0),
            ("arrival_tolerance_m", 0.0),
            ("slowdown_radius_m", 0.5),
            ("slowdown_radius_m", 0.4),
        ):
            with self.subTest(field=field):
                with self.assertRaises(OffboardRouteError):
                    self.plan(**{field: value})

    def test_decision_is_immutable(self) -> None:
        decision = self.plan()

        with self.assertRaises(FrozenInstanceError):
            decision.reached = True


if __name__ == "__main__":
    unittest.main()
