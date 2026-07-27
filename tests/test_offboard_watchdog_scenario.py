"""Scoring for the interrupted-stream SITL scenario, without SITL.

The verdict has to be re-derivable from the artifact, or it is just something a
run once printed. These tests pin what the timeline is allowed to mean: which
combinations pass, and -- more importantly -- which ones must be refused as
having failed to test anything.
"""

from __future__ import annotations

import unittest

from simulation.control.offboard_watchdog_scenario import (
    SAFE_MODES_AFTER_LOSS,
    ModeSample,
    evaluate_offboard_watchdog,
)


RECORDED_AT = "2026-07-26T12:00:00Z"


def _score(**overrides):
    values = {
        "setpoints_offered": 40,
        "setpoints_approved": 40,
        "setpoints_delivered": 40,
        "watchdog_stopped_at_s": 13.2,
        "fallback": ["zero_velocity", "hold", "land"],
        "rejections": {},
        "mode_samples": [
            ModeSample(0.5, "READY"),
            ModeSample(3.0, "TAKEOFF"),
            ModeSample(8.2, "OFFBOARD"),
            ModeSample(13.4, "HOLD"),
        ],
        "silence_began_at_s": 12.2,
        "recorded_at": RECORDED_AT,
        "fallback_executed_at_s": 13.2,
        "fallback_steps": {"completed": ["zero_velocity", "hold", "land"], "failed": {}},
    }
    values.update(overrides)
    return evaluate_offboard_watchdog(**values)


class PassingRunTests(unittest.TestCase):
    def test_a_clean_interruption_passes(self) -> None:
        report = _score()

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.mode_at_silence, "OFFBOARD")
        self.assertEqual(report.mode_after_silence, "HOLD")
        self.assertEqual(report.px4_left_offboard_at_s, 13.4)
        self.assertEqual(report.proof_level, "app+SITL")

    def test_the_document_round_trips(self) -> None:
        import json

        document = json.loads(json.dumps(_score().as_document()))

        self.assertEqual(document["scenario"], "offboard-watchdog-interruption")
        self.assertTrue(document["passed"])
        self.assertEqual(document["mode_samples"][-1]["mode"], "HOLD")

    def test_every_documented_safe_mode_is_accepted(self) -> None:
        for mode in sorted(SAFE_MODES_AFTER_LOSS):
            with self.subTest(mode=mode):
                report = _score(
                    mode_samples=[ModeSample(8.2, "OFFBOARD"), ModeSample(13.4, mode)]
                )
                self.assertTrue(report.passed, report.findings)


class RefusedRunTests(unittest.TestCase):
    """A run that failed to test anything must not be allowed to pass."""

    def test_a_run_that_never_reached_px4_fails(self) -> None:
        report = _score(setpoints_delivered=0)

        self.assertFalse(report.passed)
        self.assertIn("nothing was interrupted", " ".join(report.findings))

    def test_a_run_that_was_not_in_offboard_fails(self) -> None:
        # The most important refusal: without this, a flight that never entered
        # Offboard would "pass" by ending in HOLD, having tested nothing.
        report = _score(
            mode_samples=[ModeSample(3.0, "TAKEOFF"), ModeSample(13.4, "HOLD")],
        )

        self.assertFalse(report.passed)
        self.assertIn("not OFFBOARD", " ".join(report.findings))

    def test_a_watchdog_that_stayed_quiet_fails(self) -> None:
        report = _score(watchdog_stopped_at_s=None, fallback=[])

        self.assertFalse(report.passed)
        findings = " ".join(report.findings)
        self.assertIn("never declared the stream stopped", findings)
        self.assertIn("no fallback", findings)

    def test_a_fallback_that_does_not_stop_first_fails(self) -> None:
        report = _score(fallback=["hold", "land"])

        self.assertFalse(report.passed)
        self.assertIn("not zero_velocity", " ".join(report.findings))

    def test_staying_in_offboard_fails(self) -> None:
        # The finding a real SITL run produced: MAVSDK re-sends the last
        # setpoint at 20 Hz, so the producer going quiet does not make the link
        # go quiet. Deciding a fallback and not executing it leaves the vehicle
        # flying the last command it was given.
        report = _score(
            mode_samples=[ModeSample(8.2, "OFFBOARD")],
        )

        self.assertFalse(report.passed)
        self.assertIn("never left OFFBOARD", " ".join(report.findings))

    def test_a_fallback_that_was_only_decided_fails(self) -> None:
        report = _score(fallback_executed_at_s=None, fallback_steps=None)

        self.assertFalse(report.passed)
        self.assertIn("only decided", " ".join(report.findings))

    def test_a_fallback_that_failed_to_stop_the_vehicle_fails(self) -> None:
        # zero_velocity is the one step that stops motion without depending on
        # a mode change being accepted, so its failure is not survivable.
        report = _score(
            fallback_steps={"completed": ["hold", "land"], "failed": {"zero_velocity": "denied"}},
        )

        self.assertFalse(report.passed)
        self.assertIn("failed to stop the vehicle", " ".join(report.findings))

    def test_an_undocumented_end_mode_fails(self) -> None:
        report = _score(
            mode_samples=[ModeSample(8.2, "OFFBOARD"), ModeSample(13.4, "ACRO")],
        )

        self.assertFalse(report.passed)
        self.assertIn("not a documented safe mode", " ".join(report.findings))

    def test_an_empty_timeline_fails_rather_than_passing_vacuously(self) -> None:
        report = _score(mode_samples=[])

        self.assertFalse(report.passed)


class TimelineTests(unittest.TestCase):
    def test_the_mode_at_silence_is_the_last_one_before_it(self) -> None:
        report = _score(
            silence_began_at_s=9.0,
            mode_samples=[
                ModeSample(3.0, "TAKEOFF"), ModeSample(8.2, "OFFBOARD"), ModeSample(13.4, "HOLD")
            ],
        )

        self.assertEqual(report.mode_at_silence, "OFFBOARD")

    def test_a_mode_change_before_the_silence_does_not_count_as_leaving(self) -> None:
        # Otherwise the TAKEOFF -> OFFBOARD transition would read as PX4 having
        # already reacted to a stream that was still running.
        report = _score(
            silence_began_at_s=12.2,
            mode_samples=[
                ModeSample(3.0, "TAKEOFF"), ModeSample(8.2, "OFFBOARD"), ModeSample(14.0, "HOLD")
            ],
        )

        self.assertEqual(report.px4_left_offboard_at_s, 14.0)


if __name__ == "__main__":
    unittest.main()
