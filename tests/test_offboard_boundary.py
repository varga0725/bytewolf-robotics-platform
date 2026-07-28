"""The Offboard boundary: which streamed setpoints may reach the vehicle.

The gate's tests judge single commands. These judge a *stream*, so most of them
build a sequence and assert what the boundary does with the second setpoint
given the first: ordering, timing, and how sharply the command may change.

Every limit comes from twin.yaml. The profiles here are built by hand so a test
states the limit it is probing, but the production path reads the same fields
from the same file.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.control.boundary import OffboardBoundary, RejectionReason
from brain.control.contract import load_setpoint
from brain.safety.profile import OffboardLimits, SafetyProfile


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _limits(**overrides) -> OffboardLimits:
    values = {
        "enabled": True,
        "frame": "local_ned",
        "max_setpoint_ttl_s": 0.5,
        "max_rate_hz": 50.0,
        "max_acceleration_m_s2": 2.0,
        "max_yaw_rate_deg_s": 45.0,
        "max_uncertainty_m_s": 1.0,
        "watchdog_timeout_s": 1.0,
        "fallback_sequence": ("zero_velocity", "hold", "land"),
    }
    values.update(overrides)
    return OffboardLimits(**values)


def _profile(*, max_speed_m_s: float = 3.0, offboard: OffboardLimits | None = None) -> SafetyProfile:
    return SafetyProfile(
        vehicle_id="x500v2_reference_01",
        max_altitude_m=20.0,
        max_speed_m_s=max_speed_m_s,
        max_radius_m=2000.0,
        minimum_battery_percent_to_start=40.0,
        loss_of_link_action="RTL",
        offboard=offboard if offboard is not None else _limits(),
    )


def _setpoint(*, at: datetime = NOW, sequence: int = 1, **overrides):
    document = {
        "contract_version": "v0.1",
        "vehicle_id": "x500v2_reference_01",
        "stream_id": "stream-a",
        "sequence": sequence,
        "issued_at": at.isoformat().replace("+00:00", "Z"),
        "ttl_s": 0.2,
        "frame": "local_ned",
        "kind": "velocity",
        "validity": "valid",
        "velocity": {"x_m_s": 1.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0},
    }
    document.update(overrides)
    return load_setpoint(document)


class DefaultStateTests(unittest.TestCase):
    def test_the_boundary_is_inert_until_the_twin_enables_it(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(enabled=False)))
        decision = boundary.evaluate(_setpoint(), NOW)

        self.assertFalse(decision.approved)
        self.assertIs(decision.reason, RejectionReason.DISABLED)

    def test_a_twin_without_an_offboard_block_has_no_boundary(self) -> None:
        # Not a boundary that permits everything: a twin that never described a
        # control path does not get one by omission.
        profile = SafetyProfile(
            vehicle_id="x500v2_reference_01",
            max_altitude_m=20.0,
            max_speed_m_s=3.0,
            max_radius_m=2000.0,
            minimum_battery_percent_to_start=40.0,
            loss_of_link_action="RTL",
        )
        with self.assertRaises(ValueError):
            OffboardBoundary(profile)


class AddressingTests(unittest.TestCase):
    def test_a_setpoint_for_another_vehicle_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(vehicle_id="some_other_drone"), NOW)

        self.assertIs(decision.reason, RejectionReason.WRONG_VEHICLE)

    def test_the_wrong_frame_is_refused_not_reinterpreted(self) -> None:
        # A body_frd velocity read as local_ned is a heading-dependent sign
        # error in a movement command. There is nothing safe to infer.
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(frame="body_frd"), NOW)

        self.assertIs(decision.reason, RejectionReason.WRONG_FRAME)

    def test_a_twin_streaming_body_frd_refuses_local_ned(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(frame="body_frd")))
        self.assertIs(
            boundary.evaluate(_setpoint(frame="local_ned"), NOW).reason,
            RejectionReason.WRONG_FRAME,
        )
        self.assertTrue(boundary.evaluate(_setpoint(frame="body_frd"), NOW).approved)


class FreshnessTests(unittest.TestCase):
    def test_an_expired_setpoint_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(), NOW + timedelta(seconds=0.5))

        self.assertIs(decision.reason, RejectionReason.EXPIRED)

    def test_a_producer_cannot_grant_itself_a_longer_window(self) -> None:
        # Refused rather than clamped to the policy: a producer that thinks it
        # has two seconds of validity has misunderstood something, and quietly
        # giving it 0.5 would hide that for as long as it kept happening.
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(ttl_s=2.0), NOW)

        self.assertIs(decision.reason, RejectionReason.TTL_TOO_LONG)

    def test_a_setpoint_its_own_producer_distrusts_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(validity="invalid"), NOW)

        self.assertIs(decision.reason, RejectionReason.PRODUCER_DISTRUSTS_ITS_OWN_SETPOINT)


class OrderingTests(unittest.TestCase):
    def test_a_replayed_setpoint_cannot_reverse_a_newer_one(self) -> None:
        boundary = OffboardBoundary(_profile())
        later = NOW + timedelta(seconds=0.1)
        self.assertTrue(boundary.evaluate(_setpoint(sequence=5), NOW).approved)

        decision = boundary.evaluate(_setpoint(sequence=4, at=later), later)

        self.assertIs(decision.reason, RejectionReason.OUT_OF_ORDER)

    def test_a_duplicate_sequence_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        later = NOW + timedelta(seconds=0.1)
        boundary.evaluate(_setpoint(sequence=5), NOW)

        self.assertIs(
            boundary.evaluate(_setpoint(sequence=5, at=later), later).reason,
            RejectionReason.OUT_OF_ORDER,
        )

    def test_a_second_producer_cannot_take_over_a_live_stream(self) -> None:
        # Otherwise anything that can reach the boundary could displace the
        # controlling producer just by choosing a different stream_id.
        boundary = OffboardBoundary(_profile())
        later = NOW + timedelta(seconds=0.1)
        boundary.evaluate(_setpoint(sequence=5), NOW)

        decision = boundary.evaluate(_setpoint(stream_id="stream-b", sequence=1, at=later), later)

        self.assertIs(decision.reason, RejectionReason.STALE_STREAM)

    def test_a_new_stream_is_accepted_after_a_reset(self) -> None:
        boundary = OffboardBoundary(_profile())
        later = NOW + timedelta(seconds=0.1)
        boundary.evaluate(_setpoint(sequence=5), NOW)
        boundary.reset()

        decision = boundary.evaluate(_setpoint(stream_id="stream-b", sequence=1, at=later), later)

        self.assertTrue(decision.approved)


class RateTests(unittest.TestCase):
    def test_a_flood_of_setpoints_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_rate_hz=10.0)))
        boundary.evaluate(_setpoint(sequence=1), NOW)
        soon = NOW + timedelta(milliseconds=10)

        decision = boundary.evaluate(_setpoint(sequence=2, at=soon), soon)

        self.assertIs(decision.reason, RejectionReason.RATE_TOO_HIGH)

    def test_a_setpoint_at_the_configured_rate_is_accepted(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_rate_hz=10.0)))
        boundary.evaluate(_setpoint(sequence=1), NOW)
        later = NOW + timedelta(milliseconds=100)

        self.assertTrue(boundary.evaluate(_setpoint(sequence=2, at=later), later).approved)

    def test_an_emergency_stop_is_accepted_between_regular_stream_ticks(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_rate_hz=10.0)))
        boundary.evaluate(_setpoint(sequence=1), NOW)
        soon = NOW + timedelta(milliseconds=10)
        stopped = {
            "x_m_s": 0.0,
            "y_m_s": 0.0,
            "z_m_s": 0.0,
            "yaw_rate_deg_s": 0.0,
        }

        decision = boundary.evaluate(
            _setpoint(sequence=2, at=soon, velocity=stopped), soon
        )

        self.assertTrue(decision.approved)

    def test_repeated_zero_commands_remain_rate_limited(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_rate_hz=10.0)))
        stopped = {
            "x_m_s": 0.0,
            "y_m_s": 0.0,
            "z_m_s": 0.0,
            "yaw_rate_deg_s": 0.0,
        }
        boundary.evaluate(_setpoint(sequence=1, velocity=stopped), NOW)
        soon = NOW + timedelta(milliseconds=10)

        decision = boundary.evaluate(
            _setpoint(sequence=2, at=soon, velocity=stopped), soon
        )

        self.assertIs(decision.reason, RejectionReason.RATE_TOO_HIGH)


class EnvelopeTests(unittest.TestCase):
    def test_a_speed_over_the_limit_is_refused_not_clamped(self) -> None:
        boundary = OffboardBoundary(_profile(max_speed_m_s=3.0))
        fast = {"x_m_s": 5.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}

        decision = boundary.evaluate(_setpoint(velocity=fast), NOW)

        self.assertIs(decision.reason, RejectionReason.SPEED_TOO_HIGH)
        self.assertIsNone(decision.velocity, "a refused setpoint must not pass its velocity on")

    def test_the_limit_applies_to_the_composed_speed(self) -> None:
        # 2.5 on two axes is 3.54 m/s, past a 3 m/s limit that neither component
        # breaks on its own.
        boundary = OffboardBoundary(_profile(max_speed_m_s=3.0))
        diagonal = {"x_m_s": 2.5, "y_m_s": 2.5, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}

        self.assertIs(
            boundary.evaluate(_setpoint(velocity=diagonal), NOW).reason,
            RejectionReason.SPEED_TOO_HIGH,
        )

    def test_the_speed_limit_is_the_twin_s_own(self) -> None:
        # Not a second copy inside the offboard block: one source, so the two
        # cannot drift and let the looser one win.
        boundary = OffboardBoundary(_profile(max_speed_m_s=8.0))
        fast = {"x_m_s": 5.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}

        self.assertTrue(boundary.evaluate(_setpoint(velocity=fast), NOW).approved)

    def test_an_excessive_yaw_rate_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        spin = {"x_m_s": 0.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": -120.0}

        self.assertIs(
            boundary.evaluate(_setpoint(velocity=spin), NOW).reason,
            RejectionReason.YAW_RATE_TOO_HIGH,
        )

    def test_a_producer_that_admits_a_poor_estimate_is_refused(self) -> None:
        boundary = OffboardBoundary(_profile())
        decision = boundary.evaluate(_setpoint(uncertainty_m_s=2.5), NOW)

        self.assertIs(decision.reason, RejectionReason.TOO_UNCERTAIN)

    def test_an_omitted_uncertainty_is_a_claim_of_confidence(self) -> None:
        boundary = OffboardBoundary(_profile())
        self.assertTrue(boundary.evaluate(_setpoint(), NOW).approved)


class AccelerationTests(unittest.TestCase):
    def test_an_exact_stop_is_never_rejected_by_the_acceleration_envelope(self) -> None:
        """The shield's emergency zero must be deliverable on the next tick.

        PX4 still performs the physical deceleration; this boundary limits the
        requested stream. Rejecting the request to stop would leave MAVSDK
        re-sending the last moving setpoint at 20 Hz.
        """
        boundary = OffboardBoundary(_profile(offboard=_limits(max_acceleration_m_s2=2.0)))
        boundary.evaluate(
            _setpoint(
                sequence=1,
                velocity={
                    "x_m_s": 0.6,
                    "y_m_s": 0.0,
                    "z_m_s": 0.0,
                    "yaw_rate_deg_s": 0.0,
                },
            ),
            NOW,
        )
        later = NOW + timedelta(milliseconds=200)

        decision = boundary.evaluate(
            _setpoint(
                sequence=2,
                at=later,
                velocity={
                    "x_m_s": 0.0,
                    "y_m_s": 0.0,
                    "z_m_s": 0.0,
                    "yaw_rate_deg_s": 0.0,
                },
            ),
            later,
        )

        self.assertTrue(decision.approved)

    def test_a_near_zero_motion_is_not_misclassified_as_an_emergency_stop(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_acceleration_m_s2=2.0)))
        boundary.evaluate(
            _setpoint(
                sequence=1,
                velocity={
                    "x_m_s": 0.6,
                    "y_m_s": 0.0,
                    "z_m_s": 0.0,
                    "yaw_rate_deg_s": 0.0,
                },
            ),
            NOW,
        )
        later = NOW + timedelta(milliseconds=200)

        decision = boundary.evaluate(
            _setpoint(
                sequence=2,
                at=later,
                velocity={
                    "x_m_s": 0.01,
                    "y_m_s": 0.0,
                    "z_m_s": 0.0,
                    "yaw_rate_deg_s": 0.0,
                },
            ),
            later,
        )

        self.assertIs(decision.reason, RejectionReason.ACCELERATION_TOO_HIGH)

    def test_a_step_change_no_airframe_could_follow_is_refused(self) -> None:
        # Both endpoints are inside the speed limit; the step between them is
        # not something to discover in flight.
        boundary = OffboardBoundary(_profile(offboard=_limits(max_acceleration_m_s2=2.0)))
        boundary.evaluate(
            _setpoint(sequence=1, velocity={"x_m_s": 0.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}),
            NOW,
        )
        later = NOW + timedelta(milliseconds=100)

        decision = boundary.evaluate(
            _setpoint(
                sequence=2, at=later,
                velocity={"x_m_s": 3.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0},
            ),
            later,
        )

        self.assertIs(decision.reason, RejectionReason.ACCELERATION_TOO_HIGH)

    def test_a_gradual_change_is_accepted(self) -> None:
        boundary = OffboardBoundary(_profile(offboard=_limits(max_acceleration_m_s2=2.0)))
        boundary.evaluate(
            _setpoint(sequence=1, velocity={"x_m_s": 0.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}),
            NOW,
        )
        later = NOW + timedelta(milliseconds=100)

        decision = boundary.evaluate(
            _setpoint(
                sequence=2, at=later,
                velocity={"x_m_s": 0.15, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0},
            ),
            later,
        )

        self.assertTrue(decision.approved)

    def test_the_first_setpoint_of_a_stream_has_nothing_to_accelerate_from(self) -> None:
        boundary = OffboardBoundary(_profile())
        fast_start = {"x_m_s": 2.9, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}

        self.assertTrue(boundary.evaluate(_setpoint(velocity=fast_start), NOW).approved)


class StateAdvanceTests(unittest.TestCase):
    def test_a_rejected_setpoint_does_not_advance_the_accepted_state(self) -> None:
        # Otherwise a stream of refused commands would move the ordering and
        # timing baselines, and the next legitimate setpoint would be judged
        # against a state nothing ever approved.
        boundary = OffboardBoundary(_profile())
        boundary.evaluate(_setpoint(sequence=5), NOW)
        accepted_at = boundary.last_accepted_at

        later = NOW + timedelta(seconds=0.1)
        boundary.evaluate(_setpoint(sequence=99, at=later, ttl_s=9.0), later)

        self.assertEqual(boundary.last_accepted_at, accepted_at)
        self.assertTrue(boundary.evaluate(_setpoint(sequence=6, at=later), later).approved)


if __name__ == "__main__":
    unittest.main()
