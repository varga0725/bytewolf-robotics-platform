import unittest

from simulation.control.mission_route_scenario import (
    MissionGroundTruthSample,
    MissionRouteSample,
    evaluate_mission_route_run,
)


def _sample(
    at_s: float,
    *,
    verdict: str = "clear",
    nominal: float = 0.6,
    commanded: float = 0.6,
    delivered: bool = True,
    exact_stop: bool | None = None,
    clearance: float = 8.0,
    ground_truth_age_s: float = 0.1,
    sensed: float | None = None,
) -> MissionRouteSample:
    return MissionRouteSample(
        at_s=at_s,
        nominal_speed_m_s=nominal,
        verdict=verdict,
        commanded_speed_m_s=commanded,
        delivered=delivered,
        exact_translational_stop=(
            commanded == 0.0 if exact_stop is None else exact_stop
        ),
        clearance_m=clearance,
        ground_truth_age_s=ground_truth_age_s,
        sensed_distance_m=sensed,
    )


def _run(samples, **overrides):
    ground_truth = [
        MissionGroundTruthSample(
            at_s=sample.at_s,
            clearance_m=sample.clearance_m,
            age_s=sample.ground_truth_age_s,
        )
        for sample in samples
    ]
    values = {
        "scenario": "static-obstacle",
        "samples": samples,
        "ground_truth_samples": ground_truth,
        "required_clearance_m": 2.0,
        "flown_seconds": 1.0,
        "armed_at_s": 0.0,
        "ended_at_s": (
            ground_truth[-1].at_s if ground_truth else 1.0
        ),
        "goto_location_calls": 0,
        "fallback_completed": ("zero_velocity", "hold", "land"),
        "landed": True,
        "route_reached": False,
        "recorded_at": "2026-07-28T12:00:00Z",
    }
    values.update(overrides)
    return evaluate_mission_route_run(**values)


class MissionRouteScenarioTests(unittest.TestCase):
    def test_a_real_delivered_stop_with_clearance_and_landing_passes(self) -> None:
        report = _run(
            [
                _sample(0.0),
                _sample(0.2, clearance=5.0),
                _sample(
                    0.4,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.1,
                    sensed=3.2,
                ),
            ],
            flown_seconds=0.6,
        )

        self.assertTrue(report.passed, report.findings)
        self.assertTrue(report.delivered_stop)
        self.assertEqual(report.minimum_clearance_m, 3.1)
        self.assertEqual(report.goto_location_calls, 0)

    def test_a_computed_but_undelivered_zero_is_not_evidence(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    delivered=False,
                    clearance=3.0,
                    sensed=3.0,
                )
            ]
        )

        self.assertFalse(report.passed)
        self.assertIn("not delivered", " ".join(report.findings))

    def test_goto_location_use_fails_the_offboard_route_claim(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            goto_location_calls=1,
        )

        self.assertFalse(report.passed)
        self.assertIn("goto_location", " ".join(report.findings))

    def test_reaching_the_target_behind_the_wall_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            route_reached=True,
        )

        self.assertFalse(report.passed)
        self.assertIn("reached", " ".join(report.findings))

    def test_non_obstacle_stop_is_not_obstacle_evidence(self) -> None:
        report = _run(
            [
                _sample(0.0, verdict="no_observation", commanded=0.0),
                _sample(0.2, sensed=4.0),
            ],
            flown_seconds=0.4,
        )

        self.assertFalse(report.passed)
        self.assertIn("No blocking verdict", " ".join(report.findings))

    def test_rounded_zero_cannot_hide_a_nonzero_translation(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    exact_stop=False,
                    clearance=3.0,
                    sensed=3.0,
                )
            ]
        )

        self.assertFalse(report.passed)
        self.assertIn("No blocking verdict", " ".join(report.findings))

    def test_clearance_inside_the_twin_limit_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=1.8,
                    sensed=2.5,
                )
            ]
        )

        self.assertFalse(report.passed)
        self.assertIn("inside", " ".join(report.findings))

    def test_missing_sensor_ground_truth_or_landing_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="no_observation",
                    commanded=0.0,
                    clearance=float("nan"),
                )
            ],
            landed=False,
        )

        self.assertFalse(report.passed)
        joined = " ".join(report.findings)
        self.assertIn("sensor never", joined)
        self.assertIn("ground-truth", joined)
        self.assertIn("landing", joined)

    def test_incomplete_fallback_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            fallback_completed=("zero_velocity",),
        )

        self.assertFalse(report.passed)
        self.assertIn("fallback", " ".join(report.findings))

    def test_fallback_steps_in_the_wrong_order_fail(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            fallback_completed=("land", "hold", "zero_velocity"),
        )

        self.assertFalse(report.passed)
        self.assertIn("exact", " ".join(report.findings))

    def test_stale_ground_truth_at_the_stop_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    ground_truth_age_s=1.0,
                    sensed=3.0,
                )
            ]
        )

        self.assertFalse(report.passed)
        self.assertIn("fresh Gazebo", " ".join(report.findings))

    def test_ground_truth_must_remain_fresh_after_the_stop(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                ),
                _sample(
                    0.2,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=float("nan"),
                    ground_truth_age_s=1.0,
                    sensed=3.0,
                ),
            ],
            flown_seconds=0.4,
        )

        self.assertFalse(report.passed)
        self.assertIn("throughout", " ".join(report.findings))

    def test_terminal_clearance_violation_fails_after_safe_route_stop(self) -> None:
        samples = [
            _sample(
                0.0,
                verdict="insufficient_clearance",
                commanded=0.0,
                clearance=3.0,
                sensed=3.0,
            )
        ]
        report = _run(
            samples,
            ground_truth_samples=[
                MissionGroundTruthSample(0.0, 3.0, 0.1),
                MissionGroundTruthSample(0.2, 1.5, 0.1),
            ],
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.minimum_clearance_m, 1.5)

    def test_single_sample_cannot_cover_a_long_armed_interval(self) -> None:
        sample = _sample(
            0.0,
            verdict="insufficient_clearance",
            commanded=0.0,
            clearance=3.0,
            sensed=3.0,
        )

        report = _run([sample], ended_at_s=5.0)

        self.assertFalse(report.passed)
        self.assertIn("boundaries", " ".join(report.findings))

    def test_unexpected_executor_failure_fails_despite_safe_fallback(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            execution_error="INPUT_REJECTED: telemetry stream ended",
        )

        self.assertFalse(report.passed)
        self.assertIn("outside", " ".join(report.findings))

    def test_a_long_tick_gap_fails_even_when_average_rate_passes(self) -> None:
        samples = [
            _sample(0.0),
            _sample(0.1),
            _sample(0.2),
            _sample(0.9, verdict="insufficient_clearance", commanded=0.0, sensed=3.0),
            _sample(1.0),
            _sample(1.1),
        ]

        report = _run(samples, flown_seconds=1.2)

        self.assertFalse(report.passed)
        self.assertIn("gap", " ".join(report.findings))

    def test_a_starved_control_loop_fails(self) -> None:
        report = _run(
            [
                _sample(
                    0.0,
                    verdict="insufficient_clearance",
                    commanded=0.0,
                    clearance=3.0,
                    sensed=3.0,
                )
            ],
            flown_seconds=1.0,
        )

        self.assertFalse(report.passed)
        self.assertIn("control loop", " ".join(report.findings))


if __name__ == "__main__":
    unittest.main()
