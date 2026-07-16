"""Mission audit artifact coverage for every flight CLI."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from brain.cli import fly_return_to_home, fly_takeoff_hover_land, fly_waypoint_land
from brain.mission.execution import MissionExecution, MissionPhase


CLI_CASES = (
    (fly_takeoff_hover_land, "authorize_takeoff_hover_land", "execute"),
    (fly_waypoint_land, "authorize_takeoff_waypoint_land", "execute_waypoint_mission"),
    (fly_return_to_home, "authorize_takeoff_return_to_home", "execute_return_to_home_mission"),
)


def _mission() -> SimpleNamespace:
    return SimpleNamespace(
        takeoff=SimpleNamespace(target_altitude_m=2.0),
        hover_duration_s=3.0,
        waypoint=SimpleNamespace(north_m=5.0, east_m=0.0),
    )


class CliMissionArtifactTests(unittest.TestCase):
    def test_each_cli_writes_one_versioned_artifact_after_a_successful_mission(self) -> None:
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        for module, authorizer_name, executor_name in CLI_CASES:
            with self.subTest(cli=module.__name__), tempfile.TemporaryDirectory() as directory:
                adapter = MagicMock()
                adapter.connect = AsyncMock()
                setattr(adapter, executor_name, AsyncMock(return_value=execution))
                arguments = module.parse_arguments(
                    ("--artifact-dir", directory, "--mavsdk-server-port", "51001")
                )
                mavsdk = ModuleType("mavsdk")
                mavsdk.System = MagicMock()  # type: ignore[attr-defined]
                with (
                    patch.dict(sys.modules, {"mavsdk": mavsdk}),
                    patch.object(module, "load_safety_profile", return_value=MagicMock()),
                    patch.object(module, authorizer_name, return_value=_mission()),
                    patch.object(module, "MavsdkMissionAdapter", return_value=adapter),
                ):
                    asyncio.run(module.run(arguments))

                artifact_paths = list(Path(directory).glob("*.json"))
                self.assertEqual(len(artifact_paths), 1)
                artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
                mavsdk.System.assert_called_once_with(port=51001)
                mavsdk.System.return_value._stop_mavsdk_server.assert_called_once()
                UUID(artifact["run_id"])
                self.assertEqual(artifact["version"], "v0.2")
                self.assertEqual(artifact["events"][0]["phase"], "arming")
                self.assertEqual(artifact["safety_decision"], "approved")
                self.assertEqual(artifact["outcome"], "completed")

    def test_each_cli_writes_an_artifact_before_reraising_a_failure(self) -> None:
        for module, authorizer_name, _ in CLI_CASES:
            with self.subTest(cli=module.__name__), tempfile.TemporaryDirectory() as directory:
                arguments = module.parse_arguments(("--artifact-dir", directory))
                mavsdk = ModuleType("mavsdk")
                mavsdk.System = MagicMock()  # type: ignore[attr-defined]
                with (
                    patch.dict(sys.modules, {"mavsdk": mavsdk}),
                    patch.object(module, authorizer_name, side_effect=RuntimeError("mission rejected")),
                    self.assertRaisesRegex(RuntimeError, "mission rejected"),
                ):
                    asyncio.run(module.run(arguments))

                artifact_paths = list(Path(directory).glob("*.json"))
                self.assertEqual(len(artifact_paths), 1)
                artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
                UUID(artifact["run_id"])
                self.assertEqual(artifact["events"], [])
                self.assertEqual(artifact["safety_decision"], "rejected")
                self.assertEqual(artifact["outcome"], "failed")
                self.assertIn("mission rejected", artifact["failure_reason"])


if __name__ == "__main__":
    unittest.main()
