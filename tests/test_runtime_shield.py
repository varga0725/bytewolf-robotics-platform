"""The runtime safety shield: what it refuses, and what it honestly does not cover.

The shield is the first thing on this platform that can stop a commanded motion
because of where something *is*. So most of these tests are refusals, and the
most important ones are about absence: a sector nobody measured, an observation
that stopped arriving, a payload that is not the shape it claims.

Two tests exist to pin limits rather than capabilities -- the vertical axis and
yaw are outside what a 2D scan can speak for, and the shield must not pretend
otherwise by silently allowing or silently blocking them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.control.contract import Velocity
from brain.control.shield import (
    RuntimeSafetyShield,
    ShieldMode,
    ShieldVerdict,
)
from brain.safety.profile import ShieldLimits, load_safety_profile
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _limits(**overrides) -> ShieldLimits:
    values = {
        "enabled": True,
        "minimum_clearance_m": 2.0,
        "braking_deceleration_m_s2": 1.0,
        "reaction_latency_s": 0.5,
        "max_observation_age_s": 1.0,
        "unobserved_is_blocked": True,
    }
    values.update(overrides)
    return ShieldLimits(**values)


def _shield(mode: ShieldMode = ShieldMode.ACTIVE, **overrides) -> RuntimeSafetyShield:
    return RuntimeSafetyShield(_limits(**overrides), mode=mode)


def _obstacle(sectors, *, at: datetime = NOW, max_age_s: float = 1.0, validity: str = "valid"):
    document = {
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": at.isoformat().replace("+00:00", "Z"),
        "max_age_s": max_age_s,
        "validity": validity,
    }
    if validity != "missing":
        document["payload"] = {
            "frame": "body_frd",
            "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
            "sectors": sectors,
        }
    return load_observation(document)


def _clear(yaw_deg: float, width_deg: float = 30.0) -> dict:
    return {"yaw_deg": yaw_deg, "width_deg": width_deg, "coverage": "clear"}


def _measured(yaw_deg: float, distance_m: float, width_deg: float = 30.0) -> dict:
    return {
        "yaw_deg": yaw_deg, "width_deg": width_deg,
        "coverage": "measured", "distance_m": distance_m,
    }


def _unobserved(yaw_deg: float, width_deg: float = 30.0) -> dict:
    return {"yaw_deg": yaw_deg, "width_deg": width_deg, "coverage": "unobserved"}


FORWARD_SLOW = Velocity(0.5, 0.0, 0.0, 0.0)


class ClearPathTests(unittest.TestCase):
    def test_a_clear_heading_is_allowed_through_unchanged(self) -> None:
        decision = _shield().evaluate(FORWARD_SLOW, _obstacle([_clear(0.0)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.CLEAR)
        self.assertFalse(decision.intervened)
        self.assertEqual(decision.velocity, FORWARD_SLOW)

    def test_an_obstacle_beyond_the_stopping_distance_is_allowed(self) -> None:
        # 0.5 m/s needs 0.25 + 0.125 + 2.0 = 2.375 m. Ten metres is plenty.
        decision = _shield().evaluate(FORWARD_SLOW, _obstacle([_measured(0.0, 10.0)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.CLEAR)

    def test_an_obstacle_off_the_commanded_heading_does_not_block(self) -> None:
        # Behind the vehicle and irrelevant to a forward command; blocking on it
        # would be a false stop, which is its own kind of unsafe.
        observation = _obstacle([_clear(0.0), _measured(180.0, 0.5)])

        self.assertIs(
            _shield().evaluate(FORWARD_SLOW, observation, NOW).verdict, ShieldVerdict.CLEAR
        )


class BlockingTests(unittest.TestCase):
    def test_an_obstacle_inside_the_stopping_distance_stops_the_vehicle(self) -> None:
        decision = _shield().evaluate(FORWARD_SLOW, _obstacle([_measured(0.0, 1.0)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.INSUFFICIENT_CLEARANCE)
        self.assertEqual(decision.velocity.x_m_s, 0.0)
        self.assertEqual(decision.clearance_m, 1.0)
        self.assertEqual(decision.limiting_sector_deg, 0.0)

    def test_faster_means_blocked_sooner(self) -> None:
        # 3 m/s needs 1.5 + 4.5 + 2.0 = 8 m; 0.5 m/s needs 2.375 m. The same
        # obstacle at 5 m is fine slowly and not at speed -- which is the whole
        # reason clearance is compared against a stopping distance.
        observation = _obstacle([_measured(0.0, 5.0)])

        self.assertIs(
            _shield().evaluate(Velocity(0.5, 0.0, 0.0, 0.0), observation, NOW).verdict,
            ShieldVerdict.CLEAR,
        )
        self.assertIs(
            _shield().evaluate(Velocity(3.0, 0.0, 0.0, 0.0), observation, NOW).verdict,
            ShieldVerdict.INSUFFICIENT_CLEARANCE,
        )

    def test_the_heading_is_taken_from_both_axes(self) -> None:
        # A command 45 degrees to the right must be judged against the sector it
        # is actually heading into, not against straight ahead.
        observation = _obstacle([_clear(0.0), _measured(45.0, 1.0)])

        decision = _shield().evaluate(Velocity(0.35, 0.35, 0.0, 0.0), observation, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.INSUFFICIENT_CLEARANCE)
        self.assertEqual(decision.limiting_sector_deg, 45.0)

    def test_a_measurement_without_a_distance_is_refused(self) -> None:
        # The contract already forbids this shape, so it cannot arrive through
        # load_observation. The guard is defence in depth against an Observation
        # built some other way, and it is tested by building one that way.
        from brain.telemetry.observation import Observation

        broken = Observation(
            kind="obstacle",
            vehicle_id="x500v2_reference_01",
            observed_at=NOW,
            max_age_s=1.0,
            declared_validity="valid",
            payload={
                "frame": "body_frd",
                "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
                "sectors": [{"yaw_deg": 0.0, "width_deg": 30.0, "coverage": "measured"}],
            },
            source=None,
        )

        decision = _shield().evaluate(FORWARD_SLOW, broken, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.UNUSABLE_OBSERVATION)


class UnobservedTests(unittest.TestCase):
    """No coverage means no motion. The lidar has a 90-degree blind spot."""

    def test_an_unobserved_sector_blocks_motion_into_it(self) -> None:
        decision = _shield().evaluate(FORWARD_SLOW, _obstacle([_unobserved(0.0)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.UNOBSERVED_SECTOR)
        self.assertEqual(decision.velocity.x_m_s, 0.0)

    def test_a_bearing_no_sector_reports_on_is_unobserved(self) -> None:
        # The contract says a bearing absent from the array is unobserved, never
        # free space. Backwards flight on a 270-degree lidar is exactly this.
        observation = _obstacle([_clear(0.0), _clear(45.0), _clear(-45.0)])

        decision = _shield().evaluate(Velocity(-0.5, 0.0, 0.0, 0.0), observation, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.UNOBSERVED_SECTOR)

    def test_a_twin_may_choose_to_allow_unobserved_headings(self) -> None:
        # Off by configuration, not by accident, and recorded in the twin where
        # a reviewer can see it.
        decision = _shield(unobserved_is_blocked=False).evaluate(
            FORWARD_SLOW, _obstacle([_unobserved(0.0)]), NOW
        )

        self.assertIs(decision.verdict, ShieldVerdict.CLEAR)


class UninterpretableCoverageTests(unittest.TestCase):
    """A coverage value the shield cannot read is not free space."""

    def _sector(self, **overrides) -> dict:
        return {"yaw_deg": 0.0, "width_deg": 5.0} | overrides

    def _verdict(self, sector: dict, *, unobserved_is_blocked: bool = True):
        # Built directly rather than through load_observation: the contract
        # already refuses these shapes, which is exactly why the shield's own
        # guard needs testing by another route. If it only ever ran behind the
        # schema, nothing would show whether it protects itself.
        from brain.telemetry.observation import Observation

        observation = Observation(
            kind="obstacle",
            vehicle_id="x500v2_reference_01",
            observed_at=NOW,
            max_age_s=1.0,
            declared_validity="valid",
            payload={
                "frame": "body_frd",
                "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
                "sectors": [sector],
            },
            source=None,
        )
        shield = _shield(unobserved_is_blocked=unobserved_is_blocked)
        return shield.evaluate(FORWARD_SLOW, observation, NOW).verdict

    def test_an_unreadable_coverage_stops_the_vehicle(self) -> None:
        # The shield used to recognise "unobserved" and "measured" and let
        # everything else fall through to CLEAR -- failing open in the one
        # component whose premise is that it fails closed. The observation
        # schema rejects these today, but the shield must not rely on someone
        # else's validation for its own central rule.
        for coverage in (None, "", "unobservd", "degraded", "CLEAR", 0, True):
            with self.subTest(coverage=coverage):
                self.assertIs(
                    self._verdict(self._sector(coverage=coverage)),
                    ShieldVerdict.UNUSABLE_OBSERVATION,
                )

    def test_a_sector_without_any_coverage_stops_the_vehicle(self) -> None:
        self.assertIs(self._verdict(self._sector()), ShieldVerdict.UNUSABLE_OBSERVATION)

    def test_allowing_unobserved_does_not_allow_the_uninterpretable(self) -> None:
        # A twin may choose to trust unobserved sectors. That is a decision
        # about a value the shield understands, and it must not become blanket
        # permission for values it does not.
        self.assertIs(
            self._verdict(self._sector(coverage="unobserved"), unobserved_is_blocked=False),
            ShieldVerdict.CLEAR,
        )
        self.assertIs(
            self._verdict(self._sector(coverage="degraded"), unobserved_is_blocked=False),
            ShieldVerdict.UNUSABLE_OBSERVATION,
        )

    def test_the_three_recognised_values_still_behave(self) -> None:
        self.assertIs(self._verdict(self._sector(coverage="clear")), ShieldVerdict.CLEAR)
        self.assertIs(
            self._verdict(self._sector(coverage="unobserved")), ShieldVerdict.UNOBSERVED_SECTOR
        )
        self.assertIs(
            self._verdict(self._sector(coverage="measured", distance_m=1.0)),
            ShieldVerdict.INSUFFICIENT_CLEARANCE,
        )


class FreshnessTests(unittest.TestCase):
    def test_no_observation_at_all_stops_the_vehicle(self) -> None:
        decision = _shield().evaluate(FORWARD_SLOW, None, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.NO_OBSERVATION)
        self.assertEqual(decision.velocity.x_m_s, 0.0)

    def test_an_observation_past_its_own_age_stops_the_vehicle(self) -> None:
        stale = _obstacle([_clear(0.0)], at=NOW - timedelta(seconds=5), max_age_s=1.0)

        self.assertIs(
            _shield().evaluate(FORWARD_SLOW, stale, NOW).verdict, ShieldVerdict.STALE_OBSERVATION
        )

    def test_the_shield_may_demand_fresher_evidence_than_the_producer_allows(self) -> None:
        # A dashboard can live with a two-second-old obstacle map; something
        # deciding whether to keep moving cannot.
        generous = _obstacle([_clear(0.0)], at=NOW - timedelta(seconds=2), max_age_s=30.0)

        decision = _shield(max_observation_age_s=1.0).evaluate(FORWARD_SLOW, generous, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.STALE_OBSERVATION)

    def test_an_invalid_observation_stops_the_vehicle(self) -> None:
        invalid = _obstacle([_clear(0.0)], validity="invalid")

        self.assertIs(
            _shield().evaluate(FORWARD_SLOW, invalid, NOW).verdict, ShieldVerdict.NO_OBSERVATION
        )

    def test_a_missing_observation_is_not_an_empty_clear_map(self) -> None:
        missing = _obstacle([], validity="missing")

        self.assertIs(
            _shield().evaluate(FORWARD_SLOW, missing, NOW).verdict, ShieldVerdict.NO_OBSERVATION
        )


class ScopeTests(unittest.TestCase):
    """What a 2D scan cannot speak for, the shield must not pretend to cover."""

    def test_hovering_is_never_blocked(self) -> None:
        # Refusing here would ground a vehicle that only wanted to hold station,
        # including one being stopped by the shield itself.
        decision = _shield().evaluate(Velocity(0.0, 0.0, 0.0, 0.0), None, NOW)

        self.assertIs(decision.verdict, ShieldVerdict.CLEAR)

    def test_yaw_in_place_is_allowed_and_survives_a_stop(self) -> None:
        # Rotating carries the vehicle nowhere, so it is neither blocked nor
        # zeroed when translation is.
        turning = Velocity(0.0, 0.0, 0.0, 20.0)
        self.assertIs(_shield().evaluate(turning, None, NOW).verdict, ShieldVerdict.CLEAR)

        blocked = _shield().evaluate(Velocity(0.5, 0.0, 0.0, 20.0), _obstacle([_measured(0.0, 1.0)]), NOW)
        self.assertEqual(blocked.velocity.yaw_rate_deg_s, 20.0)
        self.assertEqual(blocked.velocity.x_m_s, 0.0)

    def test_vertical_motion_is_outside_what_this_shield_judges(self) -> None:
        # A 2D lidar scans a plane. Descending past its blind volume is not
        # something this shield can speak for, and it does not pretend to: a
        # pure descent is allowed even with an obstacle dead ahead.
        descent = Velocity(0.0, 0.0, 0.5, 0.0)

        decision = _shield().evaluate(descent, _obstacle([_measured(0.0, 0.5)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.CLEAR)


class ModeTests(unittest.TestCase):
    def test_shadow_mode_records_the_verdict_but_changes_nothing(self) -> None:
        shield = _shield(ShieldMode.SHADOW)

        decision = shield.evaluate(FORWARD_SLOW, _obstacle([_measured(0.0, 1.0)]), NOW)

        self.assertIs(decision.verdict, ShieldVerdict.INSUFFICIENT_CLEARANCE)
        self.assertTrue(decision.intervened, "the intervention is counted even in shadow")
        self.assertEqual(decision.velocity, FORWARD_SLOW, "shadow mode must not constrain")

    def test_shadow_is_the_default(self) -> None:
        self.assertIs(RuntimeSafetyShield(_limits()).mode, ShieldMode.SHADOW)

    def test_active_mode_needs_the_twin_to_agree(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeSafetyShield(_limits(enabled=False), mode=ShieldMode.ACTIVE)

    def test_the_shipped_twin_keeps_the_shield_disabled(self) -> None:
        shield = load_safety_profile().shield

        self.assertIsNotNone(shield)
        assert shield is not None
        self.assertFalse(shield.enabled)
        self.assertTrue(shield.unobserved_is_blocked)


class StoppingDistanceTests(unittest.TestCase):
    def test_a_standing_vehicle_still_wants_its_clearance(self) -> None:
        self.assertEqual(_shield().stopping_distance_m(0.0), 2.0)

    def test_it_grows_with_the_square_of_speed(self) -> None:
        # reaction + braking + clearance, and braking is the term that dominates.
        self.assertAlmostEqual(_shield().stopping_distance_m(1.0), 0.5 + 0.5 + 2.0)
        self.assertAlmostEqual(_shield().stopping_distance_m(2.0), 1.0 + 2.0 + 2.0)


if __name__ == "__main__":
    unittest.main()
