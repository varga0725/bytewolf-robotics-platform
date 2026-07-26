"""Scoring for the shield's SITL runs, without SITL.

The gate's numbers have to be re-derivable from the artifact. These pin what a
recorded run is allowed to mean -- and in particular that both failure
directions are caught, because a shield that never stops and a shield that
always stops are both broken, in opposite ways.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from simulation.control.shield_scenario import (
    ShieldSample,
    clearance_to,
    evaluate_shield_run,
)


RECORDED_AT = "2026-07-26T12:00:00Z"


def _run(samples, *, scenario="static-obstacle", mode="active", expect_intervention=True, **kwargs):
    return evaluate_shield_run(
        scenario=scenario,
        mode=mode,
        samples=samples,
        required_clearance_m=2.0,
        expect_intervention=expect_intervention,
        recorded_at=RECORDED_AT,
        **kwargs,
    )


def _clear_sample(at_s: float, clearance_m: float | None = 20.0) -> ShieldSample:
    return ShieldSample(at_s, 0.5, "clear", 0.5, clearance_m)


def _stopped_sample(at_s: float, clearance_m: float | None, verdict="insufficient_clearance"):
    # sensed_distance_m carries what the *sensor* reported. A blocking verdict
    # that never sensed anything is a run where the obstacle was never on the
    # path, which the scoring refuses to read as a shield failure.
    sensed = clearance_m if verdict == "insufficient_clearance" else None
    return ShieldSample(at_s, 0.5, verdict, 0.0, clearance_m, sensed_distance_m=sensed)


class StaticObstacleTests(unittest.TestCase):
    def test_a_run_that_stopped_in_time_passes(self) -> None:
        report = _run([_clear_sample(0.0, 10.0), _clear_sample(1.0, 5.0), _stopped_sample(2.0, 3.0)])

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.interventions, 1)
        self.assertEqual(report.minimum_clearance_m, 3.0)
        self.assertEqual(report.verdicts, {"clear": 2, "insufficient_clearance": 1})

    def test_coming_inside_the_required_clearance_fails(self) -> None:
        # The measurement that actually matters: not whether the shield fired,
        # but whether it fired in time.
        report = _run([_clear_sample(0.0, 5.0), _stopped_sample(1.0, 1.2)])

        self.assertFalse(report.passed)
        self.assertIn("came within 1.20 m", " ".join(report.findings))

    def test_a_shield_that_never_fired_fails(self) -> None:
        report = _run([_clear_sample(0.0, 10.0), _clear_sample(1.0, 3.0)])

        self.assertFalse(report.passed)
        self.assertIn("never intervened", " ".join(report.findings))


class FalseStopTests(unittest.TestCase):
    """A shield that always stops is worse than useless: it gets switched off."""

    def test_stopping_on_a_clear_path_fails(self) -> None:
        report = _run(
            [_clear_sample(0.0), _stopped_sample(1.0, 40.0)],
            scenario="clear-path",
            expect_intervention=False,
        )

        self.assertFalse(report.passed)
        findings = " ".join(report.findings)
        self.assertIn("intervened 1 times on a clear path", findings)
        self.assertIn("nothing the sensor saw can explain", findings)

    def test_a_stop_far_from_anything_is_counted_as_false(self) -> None:
        report = _run([_stopped_sample(0.0, 3.0), _stopped_sample(1.0, 40.0)])

        self.assertEqual(report.false_stops, 1)
        self.assertEqual(report.false_stop_rate, 0.5)

    def test_a_clean_clear_path_run_passes(self) -> None:
        report = _run(
            [_clear_sample(0.0), _clear_sample(1.0), _clear_sample(2.0)],
            scenario="clear-path",
            expect_intervention=False,
        )

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.intervention_rate, 0.0)


class FailClosedTests(unittest.TestCase):
    def test_a_blind_sector_stop_is_a_legitimate_intervention(self) -> None:
        # No obstacle in ground truth at all: the shield stopped because it
        # could not see, which is the rule working, not a false stop.
        report = _run(
            [_stopped_sample(0.0, None, verdict="unobserved_sector")],
            scenario="blind-sector",
        )

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.false_stops, 0)
        self.assertIsNone(report.minimum_clearance_m)

    def test_a_stale_sensor_stop_is_a_legitimate_intervention(self) -> None:
        report = _run(
            [_clear_sample(0.0, 30.0), _stopped_sample(1.0, None, verdict="stale_observation")],
            scenario="stale-sensor",
        )

        self.assertTrue(report.passed, report.findings)


class UnpresentedObstacleTests(unittest.TestCase):
    """A run that never showed the obstacle to the shield tested nothing."""

    def _run(self, samples):
        from simulation.control.shield_scenario import evaluate_shield_run

        return evaluate_shield_run(
            scenario="static-obstacle", mode="active", samples=samples,
            required_clearance_m=2.0, expect_intervention=True,
            recorded_at="2026-07-26T12:00:00Z",
            requires_sensor=True, requires_ground_truth=True, flown_seconds=22.0,
        )

    def test_a_flight_that_passed_the_box_is_inconclusive_not_a_failure(self) -> None:
        # The measured case: 110 samples, every one clear, and a ground-truth
        # clearance of 0.49 m because the vehicle went by the obstacle rather
        # than at it. Blaming the shield for that would be as wrong as passing
        # it -- the shield was never shown the thing it is judged on.
        samples = [_clear_sample(index * 0.2, clearance_m=8.0 - index * 0.07) for index in range(110)]

        report = self._run(samples)

        self.assertFalse(report.passed)
        joined = " ".join(report.findings)
        self.assertIn("did not present one to the shield", joined)
        self.assertNotIn("never intervened", joined)
        self.assertNotIn("came within", joined)

    def test_a_flight_that_did_approach_is_still_judged_on_clearance(self) -> None:
        samples = [_clear_sample(0.0, clearance_m=8.0)] + [
            _stopped_sample(index * 0.2, clearance_m=1.2) for index in range(1, 60)
        ]

        report = self._run(samples)

        self.assertFalse(report.passed)
        self.assertIn("came within", " ".join(report.findings))


class ShadowTimingTests(unittest.TestCase):
    """Shadow is judged on timing, active on outcome. They ask different things."""

    def _shadow(self, samples):
        return evaluate_shield_run(
            scenario="static-obstacle", mode="shadow", samples=samples,
            required_clearance_m=2.0, expect_intervention=True,
            recorded_at=RECORDED_AT, requires_ground_truth=True,
        )

    def test_a_shadow_run_is_not_blamed_for_where_the_vehicle_ended_up(self) -> None:
        # Nothing stopped it, by design. Its closest approach measures the
        # nominal planner; the shield is answerable for when it objected.
        observed = ShieldSample(1.0, 0.5, "insufficient_clearance", 0.5, 6.0, sensed_distance_m=6.0)
        overshot = ShieldSample(2.0, 0.5, "insufficient_clearance", 0.5, 1.2, sensed_distance_m=1.2)

        report = self._shadow([_clear_sample(0.0, 10.0), observed, overshot])

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.minimum_clearance_m, 1.2)
        self.assertEqual(report.clearance_at_first_intervention_m, 6.0)

    def test_a_shadow_run_that_objected_too_late_fails(self) -> None:
        # It would not have stopped the vehicle in time, which is precisely
        # what shadow mode exists to find out before the shield is enabled.
        late = ShieldSample(2.0, 0.5, "insufficient_clearance", 0.5, 1.5, sensed_distance_m=1.5)

        report = self._shadow([_clear_sample(0.0, 10.0), late])

        self.assertFalse(report.passed)
        self.assertIn("would not have stopped the vehicle in time", " ".join(report.findings))

    def test_an_active_run_is_judged_on_where_it_actually_stopped(self) -> None:
        report = evaluate_shield_run(
            scenario="static-obstacle", mode="active",
            samples=[_clear_sample(0.0, 10.0), _stopped_sample(1.0, 1.2)],
            required_clearance_m=2.0, expect_intervention=True,
            recorded_at=RECORDED_AT, requires_ground_truth=True,
        )

        self.assertFalse(report.passed)
        self.assertIn("came within 1.20 m", " ".join(report.findings))


class SensorFailureTests(unittest.TestCase):
    def test_a_run_where_the_sensor_never_worked_did_not_test_the_shield(self) -> None:
        # The first real attempt at this scenario "passed" with 109 straight
        # no_observation verdicts: fail-closed held, and nothing about obstacle
        # avoidance was measured.
        blind = [_stopped_sample(float(i), None, verdict="no_observation") for i in range(10)]

        report = evaluate_shield_run(
            scenario="static-obstacle", mode="shadow", samples=blind,
            required_clearance_m=2.0, expect_intervention=True,
            recorded_at=RECORDED_AT, requires_sensor=True,
        )

        self.assertFalse(report.passed)
        self.assertIn("measured fail-closed", " ".join(report.findings))


class ModeTests(unittest.TestCase):
    def test_a_shadow_run_that_changed_a_command_fails(self) -> None:
        # Shadow exists to measure without acting. One constrained command and
        # the numbers no longer describe what shadow mode does.
        constrained = ShieldSample(0.0, 0.5, "insufficient_clearance", 0.0, 3.0, sensed_distance_m=3.0)

        report = _run([constrained], mode="shadow")

        self.assertFalse(report.passed)
        self.assertIn("shadow must observe only", " ".join(report.findings))

    def test_a_shadow_run_that_only_observed_passes(self) -> None:
        observed = ShieldSample(0.0, 0.5, "insufficient_clearance", 0.5, 3.0, sensed_distance_m=3.0)

        report = _run([observed], mode="shadow")

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(report.interventions, 1, "the verdict is still counted")

    def test_an_active_run_that_kept_moving_through_a_stop_fails(self) -> None:
        ignored = ShieldSample(0.0, 0.5, "insufficient_clearance", 0.5, 3.0, sensed_distance_m=3.0)

        report = _run([ignored], mode="active")

        self.assertFalse(report.passed)
        self.assertIn("kept commanding motion", " ".join(report.findings))


class EmptyRunTests(unittest.TestCase):
    def test_a_run_with_no_samples_measured_nothing(self) -> None:
        report = _run([])

        self.assertFalse(report.passed)
        self.assertIn("measured nothing", " ".join(report.findings))


class GeometryTests(unittest.TestCase):
    def test_clearance_is_the_horizontal_distance(self) -> None:
        self.assertAlmostEqual(clearance_to((0.0, 0.0), (3.0, 4.0)), 5.0)


if __name__ == "__main__":
    unittest.main()


class LoopRateTests(unittest.TestCase):
    """A starved loop must fail the run rather than quietly weaken it."""

    def _run(self, count: int, flown_seconds: float):
        from simulation.control.shield_scenario import ShieldSample, evaluate_shield_run

        samples = [
            ShieldSample(at_s=index * 0.2, nominal_speed_m_s=0.6, verdict="clear",
                         commanded_speed_m_s=0.6, clearance_m=8.0)
            for index in range(count)
        ]
        return evaluate_shield_run(
            scenario="clear-path", mode="active", samples=samples,
            required_clearance_m=2.0, expect_intervention=False,
            recorded_at="2026-07-26T12:00:00Z", requires_sensor=True,
            flown_seconds=flown_seconds,
        )

    def test_a_starved_loop_fails_the_run(self) -> None:
        # The measured case: 26 decisions across 22 s is 1.2 Hz, and the run
        # that produced it let the vehicle inside the standoff while every
        # other number in the report looked ordinary.
        report = self._run(26, 22.0)

        self.assertAlmostEqual(report.sample_rate_hz, 1.18, places=2)
        self.assertFalse(report.passed)
        self.assertIn("starved loop", " ".join(report.findings))

    def test_a_healthy_loop_passes(self) -> None:
        report = self._run(110, 22.0)

        self.assertGreater(report.sample_rate_hz, 4.0)
        self.assertTrue(report.passed, report.findings)

    def test_without_a_duration_the_rate_is_not_guessed(self) -> None:
        report = self._run(26, None)

        self.assertIsNone(report.sample_rate_hz)
        self.assertNotIn("starved", " ".join(report.findings))


class StreamedCaptureTests(unittest.TestCase):
    """Reading the newest complete message out of a live gz capture.

    gz writes pretty-printed JSON, so the file is a stream of multi-line objects
    and the control loop is always reading it mid-write. Getting this wrong does
    not fail loudly: it hands the shield a scan that was never published.
    """

    def setUp(self) -> None:
        import tempfile

        from simulation.control.shield_run import _last_json_object

        self._read = _last_json_object
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "scans.jsonl"

    def _sec(self, text: str):
        self.path.write_text(text, encoding="utf-8")
        document = self._read(self.path)
        if not isinstance(document, dict) or "header" not in document:
            return None
        return document["header"]["stamp"]["sec"]

    @staticmethod
    def _message(sec: int) -> str:
        return (
            "{\n"
            f'  "header": {{"stamp": {{"sec": {sec}}}}},\n'
            '  "ranges": [1.0, 2.0]\n'
            "}\n"
        )

    def test_the_newest_complete_message_wins(self) -> None:
        self.assertEqual(self._sec(self._message(5) + self._message(9)), 9)

    def test_a_half_written_tail_falls_back_to_the_last_complete_one(self) -> None:
        # The defect this pins: scanning backwards for the first balanced pair
        # of braces returns a *nested* fragment of the truncated tail -- the
        # header's stamp object rather than the message -- and the shield then
        # reasons about a scan nobody published.
        partial = '{\n  "header": {"stamp": {"sec": 9}},\n  "ran'

        self.assertEqual(self._sec(self._message(5) + partial), 5)

    def test_nothing_complete_yet_reads_as_nothing(self) -> None:
        self.assertIsNone(self._sec('{\n  "header": {"stamp"'))

    def test_an_empty_capture_reads_as_nothing(self) -> None:
        self.assertIsNone(self._sec(""))

    def test_a_brace_inside_a_string_does_not_unbalance_the_scan(self) -> None:
        self.assertEqual(
            self._sec('{"note": "a } brace", "header": {"stamp": {"sec": 7}}}'), 7
        )

    def test_a_capture_that_began_mid_object_recovers(self) -> None:
        # The subprocess may be started while gz is already writing.
        self.assertEqual(self._sec('"ranges": [1.0]}\n' + self._message(4)), 4)
