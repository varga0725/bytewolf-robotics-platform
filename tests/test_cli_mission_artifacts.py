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

from brain.cli import (
    fly_controlled_interruption,
    fly_return_to_home,
    fly_takeoff_hover_land,
    fly_waypoint_land,
    fly_waypoint_square_land,
)
from brain.cli.artifacts import prepare_flight_run_recording
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
    def test_takeoff_cli_enables_the_local_live_dashboard_by_default(self) -> None:
        arguments = fly_takeoff_hover_land.parse_arguments(())

        self.assertEqual(
            arguments.dashboard_snapshot,
            Path("simulation/artifacts/dashboard/live-telemetry.json"),
        )

    def test_every_flight_cli_accepts_an_optional_read_only_telemetry_history(self) -> None:
        cases = (
            (fly_takeoff_hover_land, ()),
            (fly_waypoint_land, ()),
            (fly_return_to_home, ()),
            (fly_waypoint_square_land, ()),
            (fly_controlled_interruption, ("--interruption-action", "land")),
        )
        for module, prefix in cases:
            with self.subTest(cli=module.__name__):
                history = Path("var/mission-runs/test-history.jsonl")
                arguments = module.parse_arguments((*prefix, "--telemetry-history", str(history)))
                self.assertEqual(arguments.telemetry_history, history)

    def test_default_telemetry_histories_are_mandatory_and_isolated_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = prepare_flight_run_recording(Path(directory), None)
            second = prepare_flight_run_recording(Path(directory), None)

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.telemetry_history_path.parent.name, "telemetry-history")
        self.assertEqual(first.telemetry_history_path.stem, first.run_id)
        self.assertEqual(first.telemetry_history_path.suffix, ".jsonl")

    def test_takeoff_cli_relays_dashboard_telemetry_on_its_existing_system_connection(self) -> None:
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        relay = MagicMock()
        relay.run = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "live-telemetry.json"
            arguments = fly_takeoff_hover_land.parse_arguments(
                ("--dashboard-snapshot", str(snapshot), "--mavsdk-server-port", "51001")
            )
            mavsdk = ModuleType("mavsdk")
            mavsdk.System = MagicMock()  # type: ignore[attr-defined]
            adapter = MagicMock()
            adapter.connect = AsyncMock()
            adapter.execute = AsyncMock(return_value=execution)
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch.object(fly_takeoff_hover_land, "load_safety_profile", return_value=MagicMock()),
                patch.object(fly_takeoff_hover_land, "authorize_takeoff_hover_land", return_value=_mission()),
                patch.object(fly_takeoff_hover_land, "MavsdkMissionAdapter", return_value=adapter),
                patch.object(fly_takeoff_hover_land, "MavsdkTelemetryRelay", return_value=relay) as relay_factory,
            ):
                asyncio.run(fly_takeoff_hover_land.run(arguments))

        system = mavsdk.System.return_value
        relay_factory.assert_called_once()
        self.assertIsNotNone(relay_factory.call_args.kwargs["on_event"])
        relay.run.assert_awaited_once()
        mavsdk.System.assert_called_once_with(port=51001)
        system._stop_mavsdk_server.assert_called_once()

    def test_takeoff_cli_can_append_validated_telemetry_to_an_offline_history(self) -> None:
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        relay = MagicMock()
        relay.run = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "live-telemetry.json"
            history = Path(directory) / "mission-telemetry.jsonl"
            arguments = fly_takeoff_hover_land.parse_arguments(
                (
                    "--artifact-dir", directory, "--dashboard-snapshot", str(snapshot),
                    "--telemetry-history", str(history),
                )
            )
            mavsdk = ModuleType("mavsdk")
            mavsdk.System = MagicMock()  # type: ignore[attr-defined]
            adapter = MagicMock()
            adapter.connect = AsyncMock()
            adapter.execute = AsyncMock(return_value=execution)
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch.object(fly_takeoff_hover_land, "load_safety_profile", return_value=MagicMock()),
                patch.object(fly_takeoff_hover_land, "authorize_takeoff_hover_land", return_value=_mission()),
                patch.object(fly_takeoff_hover_land, "MavsdkMissionAdapter", return_value=adapter),
                patch.object(fly_takeoff_hover_land, "MavsdkTelemetryRelay", return_value=relay) as relay_factory,
            ):
                asyncio.run(fly_takeoff_hover_land.run(arguments))

            on_event = relay_factory.call_args.kwargs["on_event"]
            self.assertIsNotNone(on_event)
            from brain.telemetry.domain import BatteryTelemetryEvent
            from brain.telemetry.persistence import load_telemetry_history
            from datetime import UTC, datetime

            on_event(BatteryTelemetryEvent("battery", 75.0, datetime(2026, 7, 18, tzinfo=UTC)))
            artifact = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(
                load_telemetry_history(history, expected_run_id=artifact["run_id"])[0].remaining_percent,
                75.0,
            )

    def test_dashboard_relay_failure_does_not_change_the_mission_outcome(self) -> None:
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        relay = MagicMock()
        relay.run = AsyncMock(side_effect=RuntimeError("dashboard unavailable"))
        with tempfile.TemporaryDirectory() as directory:
            arguments = fly_takeoff_hover_land.parse_arguments(
                ("--dashboard-snapshot", str(Path(directory) / "live.json"),)
            )
            mavsdk = ModuleType("mavsdk")
            mavsdk.System = MagicMock()  # type: ignore[attr-defined]
            adapter = MagicMock()
            adapter.connect = AsyncMock()
            adapter.execute = AsyncMock(return_value=execution)
            with (
                patch.dict(sys.modules, {"mavsdk": mavsdk}),
                patch.object(fly_takeoff_hover_land, "load_safety_profile", return_value=MagicMock()),
                patch.object(fly_takeoff_hover_land, "authorize_takeoff_hover_land", return_value=_mission()),
                patch.object(fly_takeoff_hover_land, "MavsdkMissionAdapter", return_value=adapter),
                patch.object(fly_takeoff_hover_land, "MavsdkTelemetryRelay", return_value=relay),
            ):
                asyncio.run(fly_takeoff_hover_land.run(arguments))

        adapter.execute.assert_awaited_once()

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

    def test_each_cli_records_the_phases_reached_before_an_airborne_failure(self) -> None:
        """The trail is most useful on the failure path, so it must reach the artifact."""
        execution = (
            MissionExecution.empty()
            .transition(MissionPhase.ARMING)
            .transition(MissionPhase.TAKING_OFF)
            .transition(MissionPhase.LANDING)
            .transition(MissionPhase.FAILED)
        )
        for module, authorizer_name, executor_name in CLI_CASES:
            with self.subTest(cli=module.__name__), tempfile.TemporaryDirectory() as directory:
                adapter = MagicMock()
                adapter.connect = AsyncMock()
                adapter.execution = execution
                setattr(
                    adapter,
                    executor_name,
                    AsyncMock(side_effect=RuntimeError("low battery fallback")),
                )
                arguments = module.parse_arguments(("--artifact-dir", directory))
                mavsdk = ModuleType("mavsdk")
                mavsdk.System = MagicMock()  # type: ignore[attr-defined]
                with (
                    patch.dict(sys.modules, {"mavsdk": mavsdk}),
                    patch.object(module, "load_safety_profile", return_value=MagicMock()),
                    patch.object(module, authorizer_name, return_value=_mission()),
                    patch.object(module, "MavsdkMissionAdapter", return_value=adapter),
                    self.assertRaisesRegex(RuntimeError, "low battery fallback"),
                ):
                    asyncio.run(module.run(arguments))

                artifact_paths = list(Path(directory).glob("*.json"))
                self.assertEqual(len(artifact_paths), 1)
                artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
                self.assertEqual(
                    [event["phase"] for event in artifact["events"]],
                    ["arming", "taking_off", "landing", "failed"],
                )
                self.assertEqual(artifact["outcome"], "failed")
                self.assertIn("low battery fallback", artifact["failure_reason"])

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
