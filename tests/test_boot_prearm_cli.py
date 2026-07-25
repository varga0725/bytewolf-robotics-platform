"""Evidence-only boot and pre-arm command coverage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import check_boot_prearm
from brain.mission.artifacts import MissionTelemetrySnapshot


class BootPrearmCliTests(unittest.TestCase):
    def test_prearm_cli_allows_the_full_sitl_connection_window_by_default(self) -> None:
        self.assertEqual(check_boot_prearm.parse_arguments(()).connection_timeout, 30.0)

    def test_prearm_cli_rejects_non_positive_or_non_finite_preflight_timeout(self) -> None:
        for invalid_timeout in ("0", "-1", "nan", "inf"):
            with self.subTest(invalid_timeout=invalid_timeout), self.assertRaises(SystemExit):
                check_boot_prearm.parse_arguments(("--preflight-wait-seconds", invalid_timeout))

    def test_prearm_cli_is_evidence_only_and_writes_preflight_artifact(self) -> None:
        telemetry = MissionTelemetrySnapshot(
            captured_at=__import__("datetime").datetime(2026, 7, 16, tzinfo=__import__("datetime").UTC),
            navigation_ready=True,
            home_position_valid=True,
            global_position_valid=True,
            battery_percent=75.0,
        )
        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter.verify_preflight = AsyncMock(return_value=telemetry)
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock()  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as directory:
            arguments = check_boot_prearm.parse_arguments(
                ("--artifact-dir", directory, "--mavsdk-server-port", "51002")
            )
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch.object(check_boot_prearm, "load_safety_profile", return_value=MagicMock()),
                patch.object(check_boot_prearm, "MavsdkMissionAdapter", return_value=adapter),
            ):
                asyncio.run(check_boot_prearm.run(arguments))

            artifact_paths = tuple(Path(directory).glob("*.json"))
            self.assertEqual(len(artifact_paths), 1)
            artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))

        adapter.connect.assert_awaited_once_with("udpin://0.0.0.0:14540")
        adapter.verify_preflight.assert_awaited_once_with()
        self.assertEqual(artifact["outcome"], "completed")
        self.assertEqual(artifact["safety_decision"], "approved")
        self.assertEqual(artifact["events"], [])
        self.assertEqual(artifact["telemetry"]["battery_percent"], 75.0)
        mavsdk.System.return_value.action.assert_not_called()
        mavsdk.System.return_value._stop_mavsdk_server.assert_called_once()

    def test_prearm_cli_reports_missing_permitted_battery_without_failing_formatting(self) -> None:
        telemetry = MissionTelemetrySnapshot(
            captured_at=__import__("datetime").datetime(2026, 7, 16, tzinfo=__import__("datetime").UTC),
            navigation_ready=True,
            home_position_valid=True,
            global_position_valid=True,
            battery_percent=None,
        )
        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter.verify_preflight = AsyncMock(return_value=telemetry)
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock()  # type: ignore[attr-defined]
        with (
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
            patch.object(check_boot_prearm, "load_safety_profile", return_value=MagicMock()),
            patch.object(check_boot_prearm, "MavsdkMissionAdapter", return_value=adapter),
            patch("builtins.print") as printed,
        ):
            asyncio.run(check_boot_prearm.run(check_boot_prearm.parse_arguments(())))
        self.assertIn("battery unavailable", printed.call_args_list[-1].args[0])

    def test_prearm_cli_failure_writes_fail_closed_artifact(self) -> None:
        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter.verify_preflight = AsyncMock(side_effect=RuntimeError("pre-arm rejected"))
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock()  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as directory:
            arguments = check_boot_prearm.parse_arguments(("--artifact-dir", directory))
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch.object(check_boot_prearm, "load_safety_profile", return_value=MagicMock()),
                patch.object(check_boot_prearm, "MavsdkMissionAdapter", return_value=adapter),
                self.assertRaisesRegex(RuntimeError, "pre-arm rejected"),
            ):
                asyncio.run(check_boot_prearm.run(arguments))

            artifact = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))

        self.assertEqual(artifact["outcome"], "failed")
        self.assertEqual(artifact["safety_decision"], "rejected")
        self.assertIn("pre-arm rejected", artifact["failure_reason"])


if __name__ == "__main__":
    unittest.main()
