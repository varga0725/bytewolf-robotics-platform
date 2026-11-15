"""Every live-telemetry writer/reader must agree on the shared default path."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from brain.cli import live_telemetry_snapshot
from brain.cli.dashboard_telemetry import parse_arguments
from brain.telemetry.dashboard_snapshot_path import (
    default_dashboard_snapshot_path,
    resolve_dashboard_snapshot_path,
)


_EXPECTED = default_dashboard_snapshot_path()
_LEGACY = "simulation/artifacts/dashboard/live-telemetry.json"
_READERS_WRITERS = (
    "brain/cli/dashboard_telemetry.py",
    "brain/cli/fly_takeoff_hover_land.py",
    "brain/cli/fly_waypoint_land.py",
    "brain/cli/fly_return_to_home.py",
    "brain/cli/fly_waypoint_square_land.py",
    "brain/cli/fly_controlled_interruption.py",
    "brain/cli/fly_demo_point_rtl.py",
    "brain/cli/fly_demo_waypoint_mission.py",
    "brain/cli/fly_nim_mission.py",
    "brain/cli/ros2_telemetry_bridge.py",
    "simulation/perception/survey_recorder.py",
    "apps/api/server.py",
    "apps/gateway/plan_execution.py",
)


class DashboardSnapshotDefaultUnityTests(unittest.TestCase):
    def test_resolve_none_matches_default_helper(self) -> None:
        self.assertEqual(resolve_dashboard_snapshot_path(None), _EXPECTED)
        self.assertEqual(live_telemetry_snapshot.effective_dashboard_snapshot_path(None), _EXPECTED)

    def test_dashboard_telemetry_cli_default_is_unset(self) -> None:
        arguments = parse_arguments([])
        self.assertIsNone(arguments.snapshot_file)

    def test_modules_do_not_hardcode_legacy_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(re.escape(_LEGACY))
        for relative in _READERS_WRITERS:
            text = (root / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertIsNone(pattern.search(text), f"{relative} still references legacy default path")

    def test_cli_modules_resolve_snapshot_before_use(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in _READERS_WRITERS:
            if relative == "apps/api/server.py":
                continue
            text = (root / relative).read_text(encoding="utf-8")
            with self.subTest(module=relative):
                self.assertTrue(
                    "effective_dashboard_snapshot_path" in text or "resolve_dashboard_snapshot_path" in text,
                    f"{relative} must resolve the default through the shared helper",
                )

    def test_spawn_home_check_uses_shared_default_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "apps/gateway/plan_execution.py").read_text(encoding="utf-8")
        self.assertIn("resolve_dashboard_snapshot_path", text)
        self.assertIn("validate_plan_home_at_execution", text)

    def test_api_server_resolves_telemetry_file_in_main(self) -> None:
        tree = ast.parse(
            (Path(__file__).resolve().parents[1] / "apps/api/server.py").read_text(encoding="utf-8")
        )
        main_func = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        text = ast.get_source_segment(
            (Path(__file__).resolve().parents[1] / "apps/api/server.py").read_text(encoding="utf-8"),
            main_func,
        )
        assert text is not None
        self.assertIn("resolve_dashboard_snapshot_path", text)


if __name__ == "__main__":
    unittest.main()