import inspect
import asyncio
from datetime import UTC, datetime, timedelta
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from brain.adapters.offboard_route_executor import OffboardRouteExecutor
from simulation.control.mission_route_run import (
    AuditedAction,
    _canonical_heading,
    _ensure_termination,
    _fresh_surface_clearance,
    _simulation_environment,
)
from brain.adapters.offboard_fallback import FallbackRecord


class HeadingContractTests(unittest.TestCase):
    def test_mavsdk_heading_is_normalized_to_the_planner_contract(self) -> None:
        cases = ((0.0, 0.0), (90.0, 90.0), (180.0, 180.0), (270.0, -90.0), (360.0, 0.0))
        for heading, expected in cases:
            with self.subTest(heading=heading):
                self.assertEqual(_canonical_heading(heading), expected)

    def test_non_finite_heading_fails_closed(self) -> None:
        for heading in (math.nan, math.inf, -math.inf):
            with self.subTest(heading=heading):
                with self.assertRaises(ValueError):
                    _canonical_heading(heading)


class RouteSurfaceTests(unittest.TestCase):
    def test_route_executor_has_no_goto_location_surface(self) -> None:
        source = inspect.getsource(OffboardRouteExecutor)

        self.assertNotIn("goto_location", source)


class GroundTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "pose.json"
        self.now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    def write_pose(self, x: float, y: float = 0.0, *, age_s: float = 0.0) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "pose": [
                        {
                            "name": "x500_lidar_2d_0",
                            "position": {"x": x, "y": y},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        stamp = (self.now - timedelta(seconds=age_s)).timestamp()
        os.utime(self.path, (stamp, stamp))

    def test_clearance_is_to_the_wall_surface_not_its_centre(self) -> None:
        cases = ((8.0, 3.0), (10.0, 1.0), (12.0, 0.0))
        for y, expected in cases:
            with self.subTest(y=y):
                self.write_pose(0.0, y)
                clearance, age = _fresh_surface_clearance(self.path, self.now)
                self.assertAlmostEqual(clearance, expected)
                self.assertAlmostEqual(age, 0.0)

    def test_stale_pose_is_not_ground_truth(self) -> None:
        self.write_pose(0.0, 8.0, age_s=1.0)

        clearance, age = _fresh_surface_clearance(self.path, self.now)

        self.assertIsNone(clearance)
        self.assertAlmostEqual(age, 1.0)


class _FakeAction:
    async def goto_location(self, *args) -> None:
        return None

    async def land(self) -> None:
        return None


class ActionAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_goto_location_calls_are_measured_not_assumed(self) -> None:
        action = AuditedAction(_FakeAction())

        await action.land()
        await action.goto_location(1, 2, 3, 4)

        self.assertEqual(action.goto_location_calls, 1)


class GuiLaunchTests(unittest.TestCase):
    def test_gui_is_enabled_for_visible_acceptance_runs(self) -> None:
        environment = _simulation_environment(gui=True)

        self.assertEqual(environment["BYTEWOLF_GZ_GUI"], "1")

    def test_headless_is_an_explicit_opt_out(self) -> None:
        environment = _simulation_environment(gui=False)

        self.assertNotIn("BYTEWOLF_GZ_GUI", environment)


class _TerminationAction:
    def __init__(self) -> None:
        self.land_calls = 0

    async def land(self) -> None:
        self.land_calls += 1


class _TerminationTelemetry:
    async def in_air(self):
        yield False


class _TerminationDrone:
    def __init__(self) -> None:
        self.action = _TerminationAction()
        self.telemetry = _TerminationTelemetry()


class _TerminationFallback:
    def __init__(self, record: FallbackRecord) -> None:
        self.record = record
        self.calls = 0

    async def execute(self, _sequence):
        self.calls += 1
        return self.record


class _BlockingFallback:
    async def execute(self, _sequence):
        await asyncio.Event().wait()


class _PersistentAirTelemetry:
    async def in_air(self):
        while True:
            await asyncio.sleep(0)
            yield True


class _BlockingLandAction:
    def __init__(self) -> None:
        self.land_calls = 0

    async def land(self) -> None:
        self.land_calls += 1
        await asyncio.Event().wait()


class TerminationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fallback_retries_direct_land(self) -> None:
        drone = _TerminationDrone()
        partial = FallbackRecord(
            completed=["zero_velocity", "hold"],
            failed={"land": "transport"},
        )
        fallback = _TerminationFallback(partial)

        record, landed, error = await _ensure_termination(
            drone=drone,
            fallback_executor=fallback,
            sequence=("zero_velocity", "hold", "land"),
            fallback=partial,
            execution_error=None,
            rpc_timeout_s=0.1,
            confirm_timeout_s=0.1,
        )

        self.assertIs(record, partial)
        self.assertTrue(landed)
        self.assertEqual(drone.action.land_calls, 1)
        self.assertIn("direct emergency land", error)

    async def test_arm_attempt_without_fallback_runs_full_termination(self) -> None:
        drone = _TerminationDrone()
        complete = FallbackRecord(completed=["zero_velocity", "hold", "land"])
        fallback = _TerminationFallback(complete)

        record, landed, error = await _ensure_termination(
            drone=drone,
            fallback_executor=fallback,
            sequence=("zero_velocity", "hold", "land"),
            fallback=None,
            execution_error="arm transport failed after command",
            rpc_timeout_s=0.1,
            confirm_timeout_s=0.1,
        )

        self.assertEqual(record.completed, ["zero_velocity", "hold", "land"])
        self.assertEqual(fallback.calls, 1)
        self.assertTrue(landed)
        self.assertEqual(error, "arm transport failed after command")

    async def test_blocking_fallback_is_bounded_and_direct_land_is_attempted(self) -> None:
        drone = _TerminationDrone()

        record, landed, error = await _ensure_termination(
            drone=drone,
            fallback_executor=_BlockingFallback(),
            sequence=("zero_velocity", "hold", "land"),
            fallback=None,
            execution_error=None,
            rpc_timeout_s=0.01,
            confirm_timeout_s=0.01,
        )

        self.assertEqual(record.completed, [])
        self.assertTrue(landed)
        self.assertEqual(drone.action.land_calls, 1)
        self.assertIn("fallback executor failed", error)

    async def test_unconfirmed_completed_land_is_retried(self) -> None:
        drone = _TerminationDrone()
        drone.telemetry = _PersistentAirTelemetry()
        complete = FallbackRecord(completed=["zero_velocity", "hold", "land"])

        _record, landed, error = await _ensure_termination(
            drone=drone,
            fallback_executor=_TerminationFallback(complete),
            sequence=("zero_velocity", "hold", "land"),
            fallback=complete,
            execution_error=None,
            rpc_timeout_s=0.01,
            confirm_timeout_s=0.01,
            retry_confirm_timeout_s=0.01,
        )

        self.assertFalse(landed)
        self.assertEqual(drone.action.land_calls, 1)
        self.assertIn("retry required", error)

    async def test_blocking_direct_land_is_bounded(self) -> None:
        drone = _TerminationDrone()
        drone.action = _BlockingLandAction()
        partial = FallbackRecord(completed=["zero_velocity", "hold"])

        _record, landed, error = await _ensure_termination(
            drone=drone,
            fallback_executor=_TerminationFallback(partial),
            sequence=("zero_velocity", "hold", "land"),
            fallback=partial,
            execution_error=None,
            rpc_timeout_s=0.01,
            confirm_timeout_s=0.01,
        )

        self.assertTrue(landed)
        self.assertEqual(drone.action.land_calls, 1)
        self.assertIn("emergency land failed", error)


if __name__ == "__main__":
    unittest.main()
