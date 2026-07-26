"""The watchdog: noticing the setpoints that never arrive.

A dead producer sends nothing, so nothing it sends can raise the alarm. Every
test here is about silence -- how long is tolerated, what the vehicle is
considered to be doing meanwhile, and when the stream is declared gone.

The two clocks are the point. A setpoint expires before the stream that produced
it is declared dead, so the vehicle stops obeying an old command strictly before
anyone concludes the producer died.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.control.watchdog import OffboardWatchdog, StreamState
from brain.safety.profile import OffboardLimits, SafetyProfileError, load_safety_profile


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


class SilenceTests(unittest.TestCase):
    def test_nothing_ever_accepted_needs_no_fallback(self) -> None:
        # A fallback here would land a vehicle that is sitting on the ground.
        decision = OffboardWatchdog(_limits()).evaluate(NOW)

        self.assertIs(decision.state, StreamState.IDLE)
        self.assertFalse(decision.requires_fallback)

    def test_a_recent_setpoint_is_still_in_force(self) -> None:
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)

        decision = watchdog.evaluate(NOW + timedelta(seconds=0.1))

        self.assertIs(decision.state, StreamState.STREAMING)
        self.assertTrue(decision.state.commands_the_vehicle)

    def test_a_late_stream_expires_before_it_is_declared_dead(self) -> None:
        # The window between the two clocks: nothing is commanded, but the
        # producer may still recover with its next setpoint.
        watchdog = OffboardWatchdog(_limits(max_setpoint_ttl_s=0.5, watchdog_timeout_s=1.0))
        watchdog.record_accepted(NOW, ttl_s=0.2)

        decision = watchdog.evaluate(NOW + timedelta(seconds=0.5))

        self.assertIs(decision.state, StreamState.EXPIRED)
        self.assertFalse(decision.state.commands_the_vehicle)
        self.assertFalse(decision.requires_fallback)

    def test_silence_past_the_timeout_stops_the_stream(self) -> None:
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)

        decision = watchdog.evaluate(NOW + timedelta(seconds=1.5))

        self.assertIs(decision.state, StreamState.STOPPED)
        self.assertTrue(decision.requires_fallback)
        self.assertEqual(decision.fallback[0], "zero_velocity")

    def test_the_fallback_stops_the_vehicle_before_anything_else(self) -> None:
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)

        decision = watchdog.evaluate(NOW + timedelta(seconds=2))

        self.assertEqual(decision.fallback, ("zero_velocity", "hold", "land"))

    def test_a_backwards_clock_stops_the_stream(self) -> None:
        # Treating a negative age as freshness would extend the life of a
        # command that should already have expired.
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)

        decision = watchdog.evaluate(NOW - timedelta(seconds=5))

        self.assertIs(decision.state, StreamState.STOPPED)
        self.assertTrue(decision.requires_fallback)

    def test_a_new_setpoint_restarts_both_clocks(self) -> None:
        # Measured at a moment that would be STOPPED without the restart: 1.2 s
        # of silence is past the 1.0 s timeout. Accepting one at 0.9 s leaves
        # only 0.3 s of silence, which is expired but very much alive.
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)
        measured_at = NOW + timedelta(seconds=1.2)
        self.assertIs(watchdog.evaluate(measured_at).state, StreamState.STOPPED)

        watchdog.record_accepted(NOW + timedelta(seconds=0.9), ttl_s=0.2)

        self.assertIs(watchdog.evaluate(measured_at).state, StreamState.EXPIRED)

    def test_the_deadline_is_readable_before_it_passes(self) -> None:
        watchdog = OffboardWatchdog(_limits(watchdog_timeout_s=1.0))
        self.assertIsNone(watchdog.deadline())
        watchdog.record_accepted(NOW, ttl_s=0.2)
        self.assertEqual(watchdog.deadline(), NOW + timedelta(seconds=1.0))


class AdapterFailureTests(unittest.TestCase):
    def test_an_adapter_failure_stops_the_stream_at_once(self) -> None:
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_accepted(NOW, ttl_s=0.2)
        watchdog.record_adapter_failure("serial link closed")

        decision = watchdog.evaluate(NOW)

        self.assertIs(decision.state, StreamState.STOPPED)
        self.assertIn("serial link closed", decision.reason)

    def test_an_adapter_failure_latches(self) -> None:
        # An adapter that raised once is not trusted again because the next call
        # happened not to raise; recovery is an explicit reset.
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_adapter_failure("bus error")
        watchdog.record_accepted(NOW, ttl_s=0.2)

        self.assertIs(watchdog.evaluate(NOW).state, StreamState.STOPPED)

    def test_reset_clears_the_session(self) -> None:
        watchdog = OffboardWatchdog(_limits())
        watchdog.record_adapter_failure("bus error")
        watchdog.reset()

        self.assertIs(watchdog.evaluate(NOW).state, StreamState.IDLE)


class TwinPolicyTests(unittest.TestCase):
    """The profile loader is where an incoherent timing policy is caught."""

    def test_the_shipped_twin_declares_a_coherent_offboard_policy(self) -> None:
        offboard = load_safety_profile().offboard

        self.assertIsNotNone(offboard)
        assert offboard is not None  # narrowed for the type checker
        self.assertFalse(offboard.enabled, "the shipped twin must not enable Offboard control")
        self.assertGreater(offboard.watchdog_timeout_s, offboard.max_setpoint_ttl_s)
        self.assertEqual(offboard.fallback_sequence[0], "zero_velocity")

    def test_a_timeout_shorter_than_the_ttl_is_refused(self) -> None:
        # Otherwise the stream is declared dead while its last setpoint is still
        # in force, and the vehicle keeps flying a command already given up on.
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({"watchdog_timeout_s": 0.1})

    def test_a_fallback_that_does_not_stop_first_is_refused(self) -> None:
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({"fallback_sequence": ["hold", "land"]})

    def test_an_unknown_fallback_step_is_refused(self) -> None:
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({"fallback_sequence": ["zero_velocity", "improvise"]})

    def test_a_repeated_fallback_step_is_refused(self) -> None:
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({"fallback_sequence": ["zero_velocity", "hold", "hold"]})

    def test_an_unknown_frame_is_refused(self) -> None:
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({"frame": "enu"})

    def test_a_half_written_offboard_block_is_refused(self) -> None:
        # Every limit is required: a missing one would need a default, and a
        # default invented in code is the second source of truth this project
        # forbids.
        with self.assertRaises(SafetyProfileError):
            _load_twin_with({}, drop=["max_acceleration_m_s2"])


def _load_twin_with(overrides: dict, drop: list[str] | None = None):
    """Load the shipped twin with its offboard block altered, in a temp file."""
    import tempfile
    from pathlib import Path

    import yaml

    from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH

    document = yaml.safe_load(DEFAULT_SAFETY_PROFILE_PATH.read_text(encoding="utf-8"))
    offboard = dict(document["safety"]["offboard"])
    offboard.update(overrides)
    for field in drop or []:
        offboard.pop(field, None)
    document["safety"]["offboard"] = offboard
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "twin.yaml"
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return load_safety_profile(path)


if __name__ == "__main__":
    unittest.main()
