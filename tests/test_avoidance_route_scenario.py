import unittest

from simulation.control.avoidance_route_scenario import (
    AvoidanceGroundTruthSample,
    AvoidanceRouteSample,
    evaluate_avoidance_route_run,
)


def _sample(
    at_s: float,
    *,
    primary_verdict: str = "clear",
    selected_mode: str = "direct",
    x_m_s: float = 0.5,
    y_m_s: float = 0.0,
    z_m_s: float = 0.0,
    delivered: bool = True,
    clearance_m: float = 4.0,
    lateral_offset_m: float = 0.0,
) -> AvoidanceRouteSample:
    return AvoidanceRouteSample(
        at_s=at_s,
        primary_verdict=primary_verdict,
        selected_mode=selected_mode,
        commanded_x_m_s=x_m_s,
        commanded_y_m_s=y_m_s,
        commanded_z_m_s=z_m_s,
        delivered=delivered,
        exact_translational_stop=x_m_s == y_m_s == z_m_s == 0.0,
    )


def _run(samples, **overrides):
    ground_truth = [
        AvoidanceGroundTruthSample(
            at_s=sample.at_s,
            x_m=(sample.commanded_y_m_s / 0.4 * 3.2 if sample.commanded_y_m_s else 0.0),
            y_m=sample.at_s,
            clearance_m=4.0,
            age_s=0.1,
        )
        for sample in samples
    ]
    values = {
        "samples": samples,
        "ground_truth_samples": ground_truth,
        "flown_seconds": 1.0,
        "armed_at_s": 0.0,
        "ended_at_s": ground_truth[-1].at_s if ground_truth else 1.0,
        "required_clearance_m": 2.0,
        "required_bypass_offset_m": 3.0,
        "goto_location_calls": 0,
        "route_reached": True,
        "fallback_used": False,
        "landed": True,
        "execution_error": None,
    }
    values.update(overrides)
    return evaluate_avoidance_route_run(**values)


class AvoidanceRouteScenarioTests(unittest.TestCase):
    def test_real_stop_detour_bypass_and_goal_reach_passes(self) -> None:
        report = _run(
            [
                _sample(0.0),
                _sample(
                    0.2,
                    primary_verdict="insufficient_clearance",
                    x_m_s=0.0,
                ),
                _sample(
                    0.4,
                    primary_verdict="insufficient_clearance",
                    selected_mode="right",
                    x_m_s=0.0,
                    y_m_s=0.4,
                ),
                _sample(0.6),
            ],
            flown_seconds=0.8,
        )

        self.assertTrue(report.passed, report.findings)
        self.assertEqual(len(report.ground_truth_trace), 4)

    def test_goal_reached_without_an_obstacle_stop_fails(self) -> None:
        report = _run([_sample(0.0), _sample(0.2)], flown_seconds=0.4)

        self.assertFalse(report.passed)
        self.assertIn("blocking", " ".join(report.findings))

    def test_computed_but_undelivered_detour_fails(self) -> None:
        report = _run(
            [
                _sample(0.0, primary_verdict="insufficient_clearance", x_m_s=0.0),
                _sample(
                    0.2,
                    primary_verdict="insufficient_clearance",
                    selected_mode="right",
                    x_m_s=0.0,
                    y_m_s=0.4,
                    delivered=False,
                ),
            ],
            flown_seconds=0.4,
        )

        self.assertFalse(report.passed)
        self.assertIn("delivered lateral", " ".join(report.findings))

    def test_lateral_motion_before_the_stop_cannot_pass(self) -> None:
        report = _run(
            [
                _sample(0.0, primary_verdict="insufficient_clearance", selected_mode="right", x_m_s=0.0, y_m_s=0.4),
                _sample(0.2, primary_verdict="insufficient_clearance", x_m_s=0.0),
            ],
            flown_seconds=0.4,
        )

        self.assertFalse(report.passed)
        self.assertIn("preceded", " ".join(report.findings))

    def test_reordered_ground_truth_timestamps_fail(self) -> None:
        report = _run(
            [
                _sample(0.0, primary_verdict="insufficient_clearance", x_m_s=0.0),
                _sample(0.2, primary_verdict="insufficient_clearance", selected_mode="right", x_m_s=0.0, y_m_s=0.4),
            ],
            flown_seconds=0.4,
            ground_truth_samples=[
                AvoidanceGroundTruthSample(0.2, 0.0, 0.0, 4.0, 0.1),
                AvoidanceGroundTruthSample(0.1, 3.2, 0.2, 2.4, 0.1),
            ],
        )

        self.assertFalse(report.passed)
        self.assertIn("strictly increasing", " ".join(report.findings))

    def test_invalid_armed_boundaries_and_future_ground_truth_fail(self) -> None:
        report = _run(
            [
                _sample(0.0, primary_verdict="insufficient_clearance", x_m_s=0.0),
                _sample(0.2, primary_verdict="insufficient_clearance", selected_mode="right", x_m_s=0.0, y_m_s=0.4),
            ],
            flown_seconds=0.4,
            armed_at_s=float("nan"),
            ended_at_s=0.2,
            ground_truth_samples=[
                AvoidanceGroundTruthSample(0.0, 0.0, 0.0, 4.0, -0.1),
                AvoidanceGroundTruthSample(0.2, 3.2, 0.2, 2.4, -0.1),
            ],
        )

        self.assertFalse(report.passed)
        joined = " ".join(report.findings)
        self.assertIn("armed interval", joined)
        self.assertIn("Fresh finite", joined)

    def test_insufficient_bypass_clearance_fallback_or_goto_fails(self) -> None:
        report = _run(
            [
                _sample(0.0, primary_verdict="insufficient_clearance", x_m_s=0.0),
                _sample(
                    0.2,
                    primary_verdict="insufficient_clearance",
                    selected_mode="right",
                    x_m_s=0.0,
                    y_m_s=0.4,
                ),
            ],
            flown_seconds=0.4,
            ground_truth_samples=[
                AvoidanceGroundTruthSample(0.0, 0.0, 0.0, 1.9, 0.1),
                AvoidanceGroundTruthSample(0.2, 1.0, 0.2, 1.9, 0.1),
            ],
            goto_location_calls=1,
            fallback_used=True,
        )

        self.assertFalse(report.passed)
        joined = " ".join(report.findings)
        self.assertIn("clearance", joined)
        self.assertIn("bypass", joined)
        self.assertIn("goto_location", joined)
        self.assertIn("fallback", joined)


if __name__ == "__main__":
    unittest.main()
