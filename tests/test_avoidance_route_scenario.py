import unittest

from simulation.control.avoidance_route_scenario import (
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
        delivered=delivered,
        clearance_m=clearance_m,
        lateral_offset_m=lateral_offset_m,
    )


def _run(samples, **overrides):
    values = {
        "samples": samples,
        "flown_seconds": 1.0,
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
                    lateral_offset_m=3.2,
                    clearance_m=2.4,
                ),
                _sample(0.6, lateral_offset_m=3.2, clearance_m=2.4),
            ],
            flown_seconds=0.8,
        )

        self.assertTrue(report.passed, report.findings)

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
                    lateral_offset_m=3.2,
                ),
            ],
            flown_seconds=0.4,
        )

        self.assertFalse(report.passed)
        self.assertIn("delivered lateral", " ".join(report.findings))

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
                    lateral_offset_m=1.0,
                    clearance_m=1.9,
                ),
            ],
            flown_seconds=0.4,
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
