"""Coverage for read-only P0/P1 simulation evidence summaries."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.evidence import SimulationEvidenceError, summarize_latest_evidence


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


class SimulationEvidenceTests(unittest.TestCase):
    def test_summarizes_the_latest_valid_p0_repeatability_and_p1_dashboard_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            artifacts = Path(directory)
            _write_json(
                artifacts / "headless" / "p0-repeatability-older.json",
                {
                    "started_at": "2026-07-16T17:00:00Z",
                    "overall_status": "passed",
                    "repetitions": 2,
                    "minimum_success_rate": 0.9,
                    "nominal_scenarios": ["takeoff-hover-land"],
                    "success_rates": {"takeoff-hover-land": 1.0},
                },
            )
            _write_json(
                artifacts / "headless" / "p0-repeatability-newer.json",
                {
                    "started_at": "2026-07-16T18:00:00Z",
                    "overall_status": "passed",
                    "repetitions": 10,
                    "minimum_success_rate": 0.9,
                    "nominal_scenarios": ["takeoff-hover-land", "waypoint-land"],
                    "success_rates": {"takeoff-hover-land": 1.0, "waypoint-land": 1.0},
                },
            )
            _write_json(
                artifacts / "dashboard" / "live-telemetry.json",
                {
                    "captured_at": "2026-07-16T18:05:00Z",
                    "position": {"latitude_deg": 47.5, "longitude_deg": 19.0, "absolute_altitude_m": 125.0},
                    "battery": {"remaining_percent": 75.0},
                    "in_air": False,
                },
            )

            summary = summarize_latest_evidence(
                artifacts,
                workspace_root=artifacts,
                now=lambda: datetime(2026, 7, 16, 18, 5, 5, tzinfo=UTC),
            )

        self.assertEqual(summary["p0"]["status"], "passed")
        self.assertEqual(summary["p0"]["repetitions"], 10)
        self.assertEqual(summary["p0"]["success_rates"], {"takeoff-hover-land": 1.0, "waypoint-land": 1.0})
        self.assertEqual(summary["p1_dashboard"]["status"], "live")
        self.assertEqual(summary["p1_dashboard"]["age_seconds"], 5)
        self.assertEqual(summary["p1_dashboard"]["in_air"], False)

    def test_skips_invalid_and_failed_reports_when_selecting_latest_p0_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            artifacts = Path(directory)
            _write_json(artifacts / "headless" / "p0-repeatability-invalid.json", {"started_at": "not-a-timestamp"})
            _write_json(
                artifacts / "headless" / "p0-repeatability-failed.json",
                {
                    "started_at": "2026-07-16T19:00:00Z",
                    "overall_status": "failed",
                    "repetitions": 10,
                    "minimum_success_rate": 0.9,
                    "nominal_scenarios": ["takeoff-hover-land"],
                    "success_rates": {"takeoff-hover-land": 0.8},
                },
            )
            _write_json(
                artifacts / "headless" / "p0-repeatability-passed.json",
                {
                    "started_at": "2026-07-16T18:00:00Z",
                    "overall_status": "passed",
                    "repetitions": 10,
                    "minimum_success_rate": 0.9,
                    "nominal_scenarios": ["takeoff-hover-land"],
                    "success_rates": {"takeoff-hover-land": 1.0},
                },
            )

            summary = summarize_latest_evidence(artifacts, workspace_root=artifacts)

        self.assertEqual(summary["p0"]["report"], "headless/p0-repeatability-passed.json")
        self.assertEqual(summary["p1_dashboard"]["status"], "missing")

    def test_reports_no_valid_p0_evidence_without_raising(self) -> None:
        with TemporaryDirectory() as directory:
            summary = summarize_latest_evidence(Path(directory), workspace_root=Path(directory))

        self.assertEqual(summary["p0"]["status"], "missing")
        self.assertEqual(summary["p1_dashboard"]["status"], "missing")

    def test_rejects_an_artifact_root_outside_the_workspace_root(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SimulationEvidenceError, "artifact_root"):
                summarize_latest_evidence(Path(directory), workspace_root=Path(directory) / "workspace")


if __name__ == "__main__":
    unittest.main()
