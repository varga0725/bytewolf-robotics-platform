"""Unit tests for in-flight SITL acceptance harness (BYT-66)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from brain.mission.acceptance_inflight_ports import mavlink_endpoint_for_gcs
from brain.mission.acceptance_inflight_sitl import (
    COM_DL_LOSS_T_S,
    DATALINK_DLL_HOLD_S,
    GF_MAX_HOR_DIST_M,
    RELAY_MAX_CAPTURE_AGE_S_DEFAULT,
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
from brain.mission.acceptance_inflight_runner import TransitionRecorder, evaluate_scenario, run_one_scenario
from brain.telemetry.loopback_endpoint import LoopbackEndpointError, require_loopback_udpin_endpoint
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
        rtl_at = COM_DL_LOSS_T_S + DATALINK_DLL_HOLD_S + 1
        transitions = [
            VehicleTransition(rtl_at - 5, True, True, "auto", relative_altitude_m=20.0),
            VehicleTransition(rtl_at, True, True, "return_to_launch"),
            VehicleTransition(rtl_at + 20, False, False, "auto"),
            VehicleTransition(rtl_at + 21, False, False, "auto"),
        ]
        ok, reason = evaluate_datalink_loss_machine(transitions, heartbeat_stopped_at_s=0.0)
        self.assertTrue(ok, reason)

    def test_geofence_passes_with_breach_signal(self) -> None:
        transitions = [
            VehicleTransition(0.1, False, False, "auto"),
            VehicleTransition(1.0, True, True, "auto", horizontal_distance_from_home_m=80.0, relative_altitude_m=15.0),
            VehicleTransition(8.0, True, True, "return_to_launch", horizontal_distance_from_home_m=105.0),
            VehicleTransition(50.0, False, False, "auto", horizontal_distance_from_home_m=2.0),
            VehicleTransition(51.0, False, False, "auto"),
        ]
        ok, reason = evaluate_geofence_machine(transitions, geofence_breach_at_s=5.0)
        self.assertTrue(ok, reason)

    def test_geofence_does_not_accept_preflight_ground_as_landing(self) -> None:
        transitions = [
            VehicleTransition(0.0, False, False, "auto"),
            VehicleTransition(8.0, True, True, "return_to_launch", horizontal_distance_from_home_m=105.0),
        ]
        ok, reason = evaluate_geofence_machine(transitions, geofence_breach_at_s=5.0)
        self.assertFalse(ok)
        self.assertIn("land", reason or "")

    def test_geofence_fails_without_breach_signal(self) -> None:
        transitions = [
            VehicleTransition(5.0, True, True, "return_to_launch", horizontal_distance_from_home_m=105.0),
            VehicleTransition(50.0, False, False, "auto"),
        ]
        ok, reason = evaluate_geofence_machine(transitions, geofence_breach_at_s=None)
        self.assertFalse(ok)

    def test_geofence_fails_when_horizon_exceeded(self) -> None:
        transitions = [
            VehicleTransition(1.0, True, True, "return_to_launch", horizontal_distance_from_home_m=GF_MAX_HOR_DIST_M + 50),
            VehicleTransition(2.0, False, False, "auto"),
        ]
        ok, reason = evaluate_geofence_machine(transitions, geofence_breach_at_s=0.5)
        self.assertFalse(ok)
        self.assertIn("Horizontal", reason or "")

    def test_kill_in_air_passes(self) -> None:
        ok, reason = evaluate_kill_in_air_machine(disarm_outcome="denied", kill_to_disarm_s=0.4)
        self.assertTrue(ok, reason)

    def test_kill_in_air_fails_without_denied_disarm(self) -> None:
        ok, _ = evaluate_kill_in_air_machine(disarm_outcome="no_ack", kill_to_disarm_s=0.2)
        self.assertFalse(ok)

    def test_low_battery_ignores_preflight_ground_for_landing(self) -> None:
        transitions = [
            VehicleTransition(0.0, False, False, "auto", battery_percent=80.0),
            VehicleTransition(10.0, True, True, "auto", battery_percent=20.0),
            VehicleTransition(40.0, True, True, "auto", battery_percent=14.0),
            VehicleTransition(90.0, True, True, "return_to_launch", battery_percent=7.5),
            VehicleTransition(120.0, True, True, "auto", battery_percent=4.5),
        ]
        ok, reason = evaluate_low_battery_machine(transitions)
        self.assertFalse(ok)
        self.assertIn("land", reason or "")

    def test_low_battery_passes_ordered(self) -> None:
        transitions = [
            VehicleTransition(0.0, False, False, "auto", battery_percent=90.0),
            VehicleTransition(10.0, True, True, "auto", battery_percent=20.0),
            VehicleTransition(40.0, True, True, "auto", battery_percent=14.0),
            VehicleTransition(90.0, True, True, "return_to_launch", battery_percent=7.5),
            VehicleTransition(120.0, True, True, "auto", battery_percent=4.5),
            VehicleTransition(160.0, False, False, "auto", battery_percent=4.0),
            VehicleTransition(161.0, False, False, "auto", battery_percent=4.0),
        ]
        ok, reason = evaluate_low_battery_machine(transitions)
        self.assertTrue(ok, reason)

    def test_relay_passes_with_pattern(self) -> None:
        samples = [
            RelaySample(1.0, 0.2, False),
            RelaySample(2.0, 0.3, True),
            RelaySample(8.0, 2.5, False),
        ]
        ok, reason = evaluate_relay_inflight_machine(
            samples,
            max_age_s=RELAY_MAX_CAPTURE_AGE_S_DEFAULT,
            relay_loopback_udp_only=True,
            relay_loopback_check_performed=True,
        )
        self.assertTrue(ok, reason)


class EvidenceSchemaTests(unittest.TestCase):
    def test_build_and_validate_round_trip(self) -> None:
        from datetime import UTC, datetime

        started = datetime(2026, 1, 1, tzinfo=UTC)
        finished = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        profile = ProfilePreconditionResult(True, True, "px4-sitl-run", None)
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


class AcceptanceInflightCliTests(unittest.TestCase):
    def test_parse_arguments_default_scenario_all(self) -> None:
        from brain.cli import acceptance_inflight_sitl

        args = acceptance_inflight_sitl.parse_arguments([])
        self.assertIsNone(args.scenario)
        self.assertEqual(acceptance_inflight_sitl._resolve_scenarios(args.scenario), list(acceptance_inflight_sitl.ALL_SCENARIOS))

    def test_parse_single_scenario_only(self) -> None:
        from brain.cli import acceptance_inflight_sitl

        args = acceptance_inflight_sitl.parse_arguments(["--scenario", SCENARIO_KILL_IN_AIR])
        self.assertEqual(acceptance_inflight_sitl._resolve_scenarios(args.scenario), [SCENARIO_KILL_IN_AIR])

    def test_mavlink_endpoint_derived_from_mavsdk(self) -> None:
        self.assertEqual(
            mavlink_endpoint_for_gcs(mavsdk_endpoint="udpin://127.0.0.1:14540"),
            "udpin://127.0.0.1:14550",
        )


class AcceptanceInflightCliRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_writes_summary_and_nonzero_on_fail(self) -> None:
        from brain.cli import acceptance_inflight_sitl

        with tempfile.TemporaryDirectory() as directory:
            args = acceptance_inflight_sitl.parse_arguments(
                [
                    "--scenario",
                    SCENARIO_KILL_IN_AIR,
                    "--artifact-dir",
                    directory,
                ]
            )
            profile = ProfilePreconditionResult(False, False, "px4-run", "missing profile")

            async def fake_run_one(*_args, **_kwargs):
                return build_evidence_document(
                    scenario=SCENARIO_KILL_IN_AIR,
                    outcome="FAIL",
                    started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                    finished_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                    transitions=[],
                    profile_check=profile,
                    timings={},
                    failure_reason="profile missing",
                )

            with (
                patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}),
                patch.object(acceptance_inflight_sitl, "check_sitl_failsafe_profile_precondition", return_value=profile),
                patch.object(acceptance_inflight_sitl, "connect_adapter", new=AsyncMock()),
                patch.object(acceptance_inflight_sitl, "acquire_px4_link"),
                patch.object(acceptance_inflight_sitl, "run_one_scenario", side_effect=fake_run_one),
                patch.object(acceptance_inflight_sitl, "stop_owned_mavsdk_server"),
            ):
                code = await acceptance_inflight_sitl.run(args)
            self.assertEqual(code, 1)
            summary = Path(directory) / "summary.json"
            self.assertTrue(summary.is_file())
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "FAIL")

    async def test_run_one_scenario_records_exception_transitions(self) -> None:
        adapter = MagicMock()
        adapter._global_position_sample = AsyncMock(
            return_value=MagicMock(latitude_deg=47.0, longitude_deg=19.0, absolute_altitude_m=100.0),
        )
        profile = ProfilePreconditionResult(True, True, "run", None)
        async def seed_recorder(self) -> None:
            self._origin_s = time.monotonic()
            self.transitions.append(VehicleTransition(0.2, True, True, "auto", relative_altitude_m=20.0))

        with (
            patch(
                "brain.mission.acceptance_inflight_runner.check_sitl_failsafe_profile_precondition",
                return_value=profile,
            ),
            patch.object(TransitionRecorder, "start", seed_recorder),
            patch(
                "brain.mission.acceptance_inflight_runner._takeoff_to_altitude",
                new=AsyncMock(),
            ),
            patch(
                "brain.mission.acceptance_inflight_runner.probe_kill_in_air",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "brain.mission.acceptance_inflight_runner._safe_recover_vehicle",
                new=AsyncMock(),
            ),
        ):
            document = await run_one_scenario(
                SCENARIO_KILL_IN_AIR,
                adapter,
                system=object(),
                mavsdk_endpoint="udpin://127.0.0.1:14540",
                mavlink_endpoint="udpin://127.0.0.1:14550",
                profile_check=profile,
                snapshot_path=Path("relay.json"),
                relay_max_age_s=3.0,
                px4_run_directory=Path("run"),
                px4_build_directory=Path("build"),
            )
        self.assertEqual(document["outcome"], "FAIL")
        self.assertIn("boom", document["failure_reason"] or "")
        self.assertGreater(len(document.get("transitions") or []), 0)
        self.assertGreater(len(document.get("timings") or {}), 0)
        self.assertIn("takeoff_complete_s", document["timings"])

    async def test_profile_not_applied_never_arms(self) -> None:
        adapter = MagicMock()
        profile = ProfilePreconditionResult(False, False, None, "profile not applied")
        takeoff = AsyncMock()
        with (
            patch(
                "brain.mission.acceptance_inflight_runner.check_sitl_failsafe_profile_precondition",
                return_value=profile,
            ),
            patch("brain.mission.acceptance_inflight_runner._takeoff_to_altitude", new=takeoff),
            patch(
                "brain.mission.acceptance_inflight_runner._safe_recover_vehicle",
                new=AsyncMock(),
            ),
        ):
            document = await run_one_scenario(
                SCENARIO_LOW_BATTERY,
                adapter,
                system=object(),
                mavsdk_endpoint="udpin://127.0.0.1:14540",
                mavlink_endpoint="udpin://127.0.0.1:14550",
                profile_check=profile,
                snapshot_path=Path("relay.json"),
                relay_max_age_s=3.0,
                px4_run_directory=Path("run"),
                px4_build_directory=Path("build"),
            )
        self.assertEqual(document["outcome"], "FAIL")
        takeoff.assert_not_awaited()


class MavlinkEndpointValidationTests(unittest.TestCase):
    def test_rejects_wildcard_udpin(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://0.0.0.0:14550")

    def test_rejects_non_loopback_ip(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://192.0.2.1:14550")

    def test_rejects_hostname(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://example.com:14550")


class EvaluateScenarioDispatchTests(unittest.TestCase):
    def test_dispatch_failure_for_unknown(self) -> None:
        outcome, reason = evaluate_scenario("unknown", [], timings={}, extra={})
        self.assertEqual(outcome, "FAIL")
        self.assertIn("Unknown", reason or "")


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
            self.assertIn(exit_code, {0, 1})


if __name__ == "__main__":
    unittest.main()