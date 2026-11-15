"""Behavioural and static guards for the pre-arm SITL identity gate."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.mission.demo_errors import DemoPointRtlError


def _mock_drone() -> MagicMock:
    drone = MagicMock()
    drone.action.arm = AsyncMock()
    drone.action.takeoff = AsyncMock()
    drone.action.land = AsyncMock()
    drone.action.hold = AsyncMock()
    drone.action.set_takeoff_altitude = AsyncMock()
    drone.action.set_return_to_launch_altitude = AsyncMock()
    drone.connect = AsyncMock()
    return drone


def _assert_no_prearm_actuation(drone: MagicMock, *, extra: MagicMock | None = None) -> None:
    drone.action.arm.assert_not_awaited()
    drone.action.takeoff.assert_not_awaited()
    drone.action.land.assert_not_awaited()
    drone.action.hold.assert_not_awaited()
    drone.action.set_takeoff_altitude.assert_not_awaited()
    drone.action.set_return_to_launch_altitude.assert_not_awaited()
    if extra is not None:
        extra.assert_not_awaited()


class SimRunnerSitlRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_mission_route_sitl_refusal_sends_no_actuation(self) -> None:
        from simulation.control.mission_route_run import _fly_route

        drone = _mock_drone()
        with (
            patch("mavsdk.System", return_value=drone),
            patch(
                "simulation.control.mission_route_run._await_connection",
                new_callable=AsyncMock,
            ),
            patch(
                "simulation.control.mission_route_run._await_armable",
                new_callable=AsyncMock,
            ),
            patch(
                "simulation.control.mission_route_run.require_px4_sitl_indication",
                new_callable=AsyncMock,
                side_effect=DemoPointRtlError("not sitl"),
            ),
            patch(
                "simulation.control.mission_route_run._ensure_termination",
                new_callable=AsyncMock,
            ) as terminate,
        ):
            with tempfile.TemporaryDirectory() as tmp:
                scan = Path(tmp) / "scan.jsonl"
                pose = Path(tmp) / "pose.jsonl"
                scan.write_text("", encoding="utf-8")
                pose.write_text("", encoding="utf-8")
                report = await _fly_route(
                    altitude_m=2.0,
                    target_north_m=10.0,
                    route_timeout_s=5.0,
                    scan_path=scan,
                    pose_path=pose,
                )

        self.assertIsNotNone(report.execution_error)
        _assert_no_prearm_actuation(drone, extra=terminate)

    async def test_avoidance_route_sitl_refusal_sends_no_actuation(self) -> None:
        from simulation.control.avoidance_route_run import _fly_avoidance_route

        drone = _mock_drone()
        with (
            patch("mavsdk.System", return_value=drone),
            patch(
                "simulation.control.avoidance_route_run._await_connection",
                new_callable=AsyncMock,
            ),
            patch(
                "simulation.control.avoidance_route_run._await_armable",
                new_callable=AsyncMock,
            ),
            patch(
                "simulation.control.avoidance_route_run.require_px4_sitl_indication",
                new_callable=AsyncMock,
                side_effect=DemoPointRtlError("not sitl"),
            ),
            patch(
                "simulation.control.avoidance_route_run._ensure_termination",
                new_callable=AsyncMock,
            ) as terminate,
            patch(
                "simulation.control.avoidance_route_run._normal_land_or_escalate",
                new_callable=AsyncMock,
            ) as normal_land,
        ):
            with tempfile.TemporaryDirectory() as tmp:
                scan = Path(tmp) / "scan.jsonl"
                pose = Path(tmp) / "pose.jsonl"
                scan.write_text("", encoding="utf-8")
                pose.write_text("", encoding="utf-8")
                report = await _fly_avoidance_route(
                    altitude_m=2.0,
                    target_north_m=10.0,
                    route_timeout_s=5.0,
                    scan_path=scan,
                    pose_path=pose,
                    route_speed_m_s=0.8,
                )

        self.assertIsNotNone(report.execution_error)
        _assert_no_prearm_actuation(drone, extra=terminate)
        normal_land.assert_not_awaited()


class ProductionArmAstGuardTests(unittest.TestCase):
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    _SKIP_PARTS = frozenset({"tests", "artifacts", ".venv"})
    _ALLOW_UNGATED_FILES = frozenset(
        {
            "brain/adapters/mavsdk_adapter.py",
            "brain/adapters/demo_waypoint_mission_adapter.py",
        }
    )

    def _iter_production_python_files(self) -> list[Path]:
        paths: list[Path] = []
        for path in self._REPO_ROOT.rglob("*.py"):
            if any(part in self._SKIP_PARTS for part in path.parts):
                continue
            paths.append(path)
        return paths

    def test_production_arm_calls_require_sitl_gate_in_same_function(self) -> None:
        offenders: list[str] = []
        for path in self._iter_production_python_files():
            rel = path.relative_to(self._REPO_ROOT).as_posix()
            if rel in self._ALLOW_UNGATED_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                arm_lines = [
                    sub.lineno
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Await)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Attribute)
                    and sub.value.func.attr == "arm"
                    and isinstance(sub.value.func.value, ast.Attribute)
                    and sub.value.func.value.attr == "action"
                ]
                if not arm_lines:
                    continue
                gate_lines = [
                    sub.lineno
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Await)
                    and isinstance(sub.value, ast.Call)
                    and (
                        (
                            isinstance(sub.value.func, ast.Name)
                            and sub.value.func.id == "require_px4_sitl_indication"
                        )
                        or (
                            isinstance(sub.value.func, ast.Attribute)
                            and sub.value.func.attr == "_require_px4_sitl_before_arm"
                        )
                    )
                ]
                if not gate_lines or min(gate_lines) > min(arm_lines):
                    offenders.append(f"{rel}:{node.name} (arm@{min(arm_lines)})")
        self.assertEqual(offenders, [], f"ungated arm() in: {offenders}")


if __name__ == "__main__":
    unittest.main()