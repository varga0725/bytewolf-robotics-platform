"""Tallying the shield's repeated runs. Pure, so it needs no SITL.

One pass is not a result; these pin what a set of runs is allowed to conclude.
The thresholds are the point: a safety behaviour that holds nine times out of
ten is not a safety behaviour, and the tally must say so.
"""

from __future__ import annotations

import unittest

from simulation.control.shield_repeatability import THRESHOLDS, _crashed_run, tally_scenario
from simulation.control.shield_scenario import ShieldScenarioReport


def _report(passed: bool, *, clearance=None, first=None, false_stop_rate=0.0, findings=()):
    return ShieldScenarioReport(
        scenario="static-obstacle", recorded_at="2026-07-26T12:00:00Z", mode="active",
        samples=100, interventions=10, intervention_rate=0.1,
        false_stops=0, false_stop_rate=false_stop_rate,
        minimum_clearance_m=clearance, required_clearance_m=2.0,
        clearance_at_first_intervention_m=first,
        passed=passed, findings=list(findings),
    )


class ThresholdTests(unittest.TestCase):
    def test_a_safety_behaviour_must_hold_every_time(self) -> None:
        for scenario in ("static-obstacle", "blind-sector", "stale-sensor"):
            with self.subTest(scenario=scenario):
                self.assertEqual(THRESHOLDS[scenario], 1.0)

    def test_nine_out_of_ten_fails_a_safety_scenario(self) -> None:
        runs = [_report(True)] * 9 + [_report(False, findings=["came within 1.2 m"])]

        tally = tally_scenario("static-obstacle", runs)

        self.assertEqual(tally.success_rate, 0.9)
        self.assertFalse(tally.met)
        self.assertIn("run 10", tally.failures[0])

    def test_nine_out_of_ten_meets_the_nominal_threshold(self) -> None:
        runs = [_report(True)] * 9 + [_report(False, findings=["one flake"])]

        self.assertTrue(tally_scenario("clear-path", runs).met)

    def test_ten_out_of_ten_passes(self) -> None:
        tally = tally_scenario("static-obstacle", [_report(True)] * 10)

        self.assertTrue(tally.met)
        self.assertEqual(tally.failures, [])


class WorstCaseTests(unittest.TestCase):
    def test_the_worst_clearance_across_runs_is_reported(self) -> None:
        # A reader wants the closest the vehicle ever came, not an average that
        # would hide the one run that nearly hit something.
        runs = [_report(True, clearance=3.5), _report(True, clearance=2.6), _report(True, clearance=4.0)]

        self.assertEqual(tally_scenario("static-obstacle", runs).worst_clearance_m, 2.6)

    def test_the_latest_first_intervention_is_reported(self) -> None:
        # A first objection drifting inward across runs is the early warning
        # that the margin is marginal.
        runs = [_report(True, first=3.6), _report(True, first=2.4)]

        self.assertEqual(tally_scenario("static-obstacle", runs).worst_first_intervention_m, 2.4)

    def test_the_highest_false_stop_rate_is_reported(self) -> None:
        runs = [_report(True, false_stop_rate=0.0), _report(True, false_stop_rate=0.05)]

        self.assertEqual(tally_scenario("clear-path", runs).highest_false_stop_rate, 0.05)

    def test_runs_without_a_clearance_do_not_invent_one(self) -> None:
        tally = tally_scenario("blind-sector", [_report(True), _report(True)])

        self.assertIsNone(tally.worst_clearance_m)


class CrashedRunTests(unittest.TestCase):
    """A run that dies is data, not a reason to abandon the matrix."""

    def test_a_crashed_run_counts_as_a_failure(self) -> None:
        crashed = _crashed_run("static-obstacle", "active", RuntimeError("COMMAND_DENIED"))

        self.assertFalse(crashed.passed)
        self.assertIn("COMMAND_DENIED", crashed.findings[0])
        self.assertIsNone(crashed.minimum_clearance_m, "a crash measured no clearance")

    def test_one_crash_fails_a_safety_scenario_without_hiding_the_rest(self) -> None:
        # The matrix must keep going and then say so, rather than one run
        # forty seconds in taking the other thirty-nine with it.
        runs = [_report(True)] * 9 + [
            _crashed_run("static-obstacle", "active", RuntimeError("COMMAND_DENIED"))
        ]

        tally = tally_scenario("static-obstacle", runs)

        self.assertEqual(tally.passed, 9)
        self.assertFalse(tally.met)
        self.assertIn("did not complete", tally.failures[0])

    def test_a_crash_does_not_erase_the_worst_case_from_the_runs_that_flew(self) -> None:
        runs = [
            _report(True, clearance=3.2),
            _crashed_run("static-obstacle", "active", RuntimeError("boom")),
        ]

        self.assertEqual(tally_scenario("static-obstacle", runs).worst_clearance_m, 3.2)


class EmptyTests(unittest.TestCase):
    def test_no_runs_meets_nothing(self) -> None:
        tally = tally_scenario("static-obstacle", [])

        self.assertFalse(tally.met)
        self.assertEqual(tally.success_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
