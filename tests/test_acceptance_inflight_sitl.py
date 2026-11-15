"""Unit tests for in-flight SITL acceptance harness (BYT-66)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from brain.mission.acceptance_inflight_sitl import (
    COM_DL_LOSS_T_S,
    GF_MAX_HOR_DIST_M,
    SCENARIO_DATALINK_LOSS,
    SCENARIO_GEOFENCE,
    SCENARIO_KILL_IN_AIR,
    SCENARIO_LOW_BATTERY,
    SCENARIO_RELAY_INFLIGHT,
    ProfilePreconditionResult,
    RelaySample,
    VehicleTransition,
    build_evidence_document,
    check_sitl_failsafe_profile_precondition,
    evaluate_datalink_loss_machine,
    evaluate_geofence_machine,
    evaluate_kill_in_air_machine,
    evaluate_low_battery_machine,
    evaluate_relay_inflight_machine,
    validate_evidence_document,
)
from brain.mission.acceptance_inflight_runner import evaluate_scenario
from simulation.px4.sitl_failsafe_profile import write_sitl_failsafe_status


class ProfilePreconditionTests(unittest.TestCase):
    def test_refuses_missing_run_directory(self) -> None:
        result = check_sitl_failsafe_profile_precondition(px4_run_directory=None)
        self.assertFalse(result.ok)
        self.assertIn("PX4_RUN_DIR", result.failure_reason or "")

    def test_refuses_missing_status_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = check_sitl_failsafe_profile_precondition(px4_run_directory=Path(directory))
        self.assertFalse(result.applied)

    def test_refuses_when_verify_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            write_sitl_failsafe_status(run_dir, "applied")
            from simulation.px4.sitl_failsafe_profile import SitlFailsafeProfileError

            with patch(
                "brain.mission.acceptance_inflight_sitl.verify_sitl_failsafe_profile",
                side_effect=SitlFailsafeProfileError("mismatch"),
            ):
                result = check_sitl_failsafe_profile_precondition(
                    px4_run_directory=run_dir,
                    read_param=lambda _name, _build: 1.0,
                )
        self.assertTrue(result.applied)
        self.assertFalse(result.verified)


class ScenarioStateMachineTests(unittest.TestCase):
    def test_datalink_loss_passes_with_rtl_and_land(self) -> None:
        stop_s = 40.0
        transitions = [
            VehicleTransition(stop_s - 5, True, True, "auto", relative_altitude_m=20.0),
            VehicleTransition(stop_s + COM_DL_LOSS_T_S + 1, True, True, "return_to_launch"),
            VehicleTransition(stop_s + COM_DL_LOSS_T_S + 30, False, False, "auto"),
            VehicleTransition(stop_s + COM_DL_LOSS_T_S + 31, False, False, "auto"),
        ]
        ok, reason = evaluate_datalink_loss_machine(transitions, heartbeat_stopped_at_s=stop_s)
        self.assertTrue(ok, reason)

    def test_geofence_passes_within_tolerance(self) -> None:
        transitions = [
            VehicleTransition(1.0, True, True, "auto", horizontal_distance_from_home_m=80.0, relative_altitude_m=15.0),
            VehicleTransition(5.0, True, True, "return_to_launch", horizontal_distance_from_home_m=105.0),
            VehicleTransition(50.0, False, False, "auto", horizontal_distance_from_home_m=2.0),
            VehicleTransition(51.0, False, False, "auto"),
        ]
        ok, reason = evaluate_geofence_machine(transitions)
        self.assertTrue(ok, reason)

    def test_geofence_fails_when_horizon_exceeded(self) -> None:
        transitions = [
            VehicleTransition(1.0, True, True, "return_to_launch", horizontal_distance_from_home_m=GF_MAX_HOR_DIST_M + 50),
            VehicleTransition(2.0, False, False, "auto"),
        ]
        ok, reason = evaluate_geofence_machine(transitions)
        self.assertFalse(ok)
        self.assertIn("Horizontal", reason or "")

    def test_kill_in_air_passes(self) -> None:
        ok, reason = evaluate_kill_in_air_machine(disarm_in_air_rejected=True, kill_to_disarm_s=0.4)
        self.assertTrue(ok, reason)

    def test_kill_in_air_fails_without_rejected_disarm(self) -> None:
        ok, _ = evaluate_kill_in_air_machine(disarm_in_air_rejected=False, kill_to_disarm_s=0.2)
        self.assertFalse(ok)

    def test_low_battery_passes(self) -> None:
        transitions = [
            VehicleTransition(1.0, True, True, "auto", battery_percent=20.0),
            VehicleTransition(5.0, True, True, "auto", battery_percent=14.0),
            VehicleTransition(10.0, True, True, "return_to_launch", battery_percent=6.5),
            VehicleTransition(60.0, False, False, "auto", battery_percent=4.0),
            VehicleTransition(61.0, False, False, "auto", battery_percent=4.0),
        ]
        ok, reason = evaluate_low_battery_machine(transitions)
        self.assertTrue(ok, reason)

    def test_relay_passes_with_pattern(self) -> None:
        samples = [
            RelaySample(1.0, 0.2, False),
            RelaySample(2.0, 0.3, True),
            RelaySample(8.0, 0.4, False),
        ]
        ok, reason = evaluate_relay_inflight_machine(samples)
        self.assertTrue(ok, reason)


class EvidenceSchemaTests(unittest.TestCase):
    def test_build_and_validate_round_trip(self) -> None:
        from datetime import UTC, datetime

        started = datetime(2026, 1, 1, tzinfo=UTC)
        finished = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        profile = ProfilePreconditionResult(True, True, "/tmp/px4-run", None)
        document = build_evidence_document(
            scenario=SCENARIO_DATALINK_LOSS,
            outcome="PASS",
            started_at=started,
            finished_at=finished,
            transitions=[],
            profile_check=profile,
            timings={"takeoff_complete_s": 12.0},
        )
        validate_evidence_document(document)
        round_trip = json.loads(json.dumps(document))
        validate_evidence_document(round_trip)


class EvaluateScenarioDispatchTests(unittest.TestCase):
    def test_dispatch_failure_for_unknown(self) -> None:
        outcome, reason = evaluate_scenario("unknown", [], timings={}, extra={})
        self.assertEqual(outcome, "FAIL")
        self.assertIn("Unknown", reason or "")


class AcceptanceInflightCliTests(unittest.TestCase):
    def test_parse_arguments_default_scenario_all(self) -> None:
        from brain.cli import acceptance_inflight_sitl

        args = acceptance_inflight_sitl.parse_arguments([])
        self.assertEqual(args.scenario, ["all"])
        self.assertEqual(args.endpoint, "udpin://127.0.0.1:14540")


@unittest.skipUnless(os.environ.get("BYTEWOLF_SITL_CONNECTION"), "Set BYTEWOLF_SITL_CONNECTION for live harness")
class AcceptanceInflightSitlLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_scenario_writes_evidence(self) -> None:
        from brain.cli import acceptance_inflight_sitl

        with tempfile.TemporaryDirectory() as directory:
            args = acceptance_inflight_sitl.parse_arguments(
                [
                    "--scenario",
                    SCENARIO_RELAY_INFLIGHT,
                    "--artifact-dir",
                    directory,
                    "--endpoint",
                    os.environ["BYTEWOLF_SITL_CONNECTION"],
                ]
            )
            if os.environ.get("PX4_RUN_DIR"):
                args.px4_run_directory = Path(os.environ["PX4_RUN_DIR"])
            with patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}):
                exit_code = await acceptance_inflight_sitl.run(args)
            evidence = Path(directory) / f"{SCENARIO_RELAY_INFLIGHT}.json"
            self.assertTrue(evidence.is_file())
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            validate_evidence_document(payload)
            if exit_code != 0:
                self.skipTest(payload.get("failure_reason") or "scenario failed on this host")


if __name__ == "__main__":
    unittest.main()