"""The mission replay API only reads validated, durable run evidence."""

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from apps.api.server import create_app
from brain.mission.artifacts import MissionArtifactWriter, MissionAuditArtifact
from brain.mission.execution import MissionExecution, MissionPhase
from brain.telemetry.domain import BatteryTelemetryEvent
from brain.telemetry.persistence import TelemetryHistoryStore


class MissionReplayApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.runs = self.root / "mission-runs"
        telemetry = self.root / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.client = TestClient(create_app(telemetry, mission_runs_dir=self.runs))
        self.recorded_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _record_run(self, run_id: str = "run-20260725-a") -> None:
        execution = (
            MissionExecution.empty()
            .transition(MissionPhase.ARMING, self.recorded_at)
            .transition(MissionPhase.FAILED, datetime(2026, 7, 25, 10, 0, 2, tzinfo=UTC))
        )
        MissionArtifactWriter(self.runs).write(MissionAuditArtifact.from_execution(
            run_id,
            execution,
            recorded_at=datetime(2026, 7, 25, 10, 0, 3, tzinfo=UTC),
            safety_decision="approved",
            outcome="failed",
            failure_reason="test fallback",
        ))
        TelemetryHistoryStore(self.runs / "telemetry-history" / f"{run_id}.jsonl", run_id=run_id).append(
            BatteryTelemetryEvent("telemetry/battery", 72.5, self.recorded_at)
        )

    def test_lists_validated_replays_without_paths_or_a_session(self) -> None:
        self._record_run()

        response = self.client.get("/api/v1/missions/replays")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"replays": [{
            "id": "run-20260725-a",
            "recorded_at": "2026-07-25T10:00:03Z",
            "safety_decision": "approved",
            "outcome": "failed",
            "terminal_phase": "failed",
        }], "unreadable": 0})
        self.assertNotIn(str(self.runs), response.text)

    def test_reads_one_immutable_replay_with_audited_timeline_and_telemetry(self) -> None:
        self._record_run()

        response = self.client.get("/api/v1/missions/replays/run-20260725-a")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "id": "run-20260725-a",
            "recorded_at": "2026-07-25T10:00:03Z",
            "safety_decision": "approved",
            "outcome": "failed",
            "failure_reason": "test fallback",
            "terminal_phase": "failed",
            "preflight": {
                "battery_percent": None,
                "navigation_ready": None,
                "home_position_valid": None,
                "global_position_valid": None,
            },
            "events": [
                {"phase": "arming", "timestamp": "2026-07-25T10:00:00Z"},
                {"phase": "failed", "timestamp": "2026-07-25T10:00:02Z"},
            ],
            "telemetry": [{
                "type": "battery",
                "topic": "telemetry/battery",
                "observed_at": "2026-07-25T10:00:00Z",
                "remaining_percent": 72.5,
            }],
        })

    def test_refuses_traversal_unknown_and_invalid_artifacts_without_leaking_paths(self) -> None:
        self._record_run()
        self.runs.joinpath("broken.json").write_text("{", encoding="utf-8")

        traversal = self.client.get("/api/v1/missions/replays/..%5Cbroken")
        missing = self.client.get("/api/v1/missions/replays/does-not-exist")
        invalid = self.client.get("/api/v1/missions/replays/broken")
        listing = self.client.get("/api/v1/missions/replays")

        self.assertEqual(traversal.status_code, 400)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid.status_code, 503)

        # The listing used to 503 as well, so as not to "partially advertise
        # unvalidated audit history". The intent was right and the mechanism
        # was too blunt: a mission that fails before its telemetry relay starts
        # -- MAVSDK unavailable, the SafetyGate refusing, PX4 timing out --
        # still writes its audit artifact from a `finally`, with an empty
        # history beside it. That is an ordinary outcome, and it made the whole
        # archive permanently unavailable behind the first one.
        #
        # The intent is kept by counting rather than by refusing: valid replays
        # are served, and the count says how many could not be read, so nothing
        # is hidden without saying so.
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        self.assertEqual(len(body["replays"]), 1, "the readable run is still served")
        self.assertEqual(body["unreadable"], 1, "and the unreadable one is declared")
        for response in (traversal, missing, invalid, listing):
            self.assertNotIn(str(self.runs), response.text)

    def test_has_no_flight_control_or_mutation_method(self) -> None:
        self._record_run()

        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method("/api/v1/missions/replays/run-20260725-a").status_code, 405)


if __name__ == "__main__":
    unittest.main()
