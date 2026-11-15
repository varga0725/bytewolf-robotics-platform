"""Approve-time SITL identity re-check before spawning mission CLIs."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from apps.gateway import plan_execution
from apps.gateway.plan_execution import execute_reviewed_plan
from brain.mission.sitl_target import TargetClassification


class PlanExecutionSitlGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.plan_dir = Path(self.directory.name)
        self.plan_name = "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json"
        self.plan_path = self.plan_dir / self.plan_name
        self.plan_path.write_text('{"schema_version":"mission-spec-v0.1"}', encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()
        plan_execution._active_execution = None

    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    @patch("apps.gateway.plan_execution.subprocess.Popen")
    @patch("apps.gateway.plan_execution.TargetClassifier.require_sitl_verified")
    @patch("apps.gateway.plan_execution._validate_plan_before_spawn")
    def test_refuses_when_classifier_not_verified(self, _validate, require_verified, popen) -> None:
        with patch.object(plan_execution, "_DEFAULT_PLAN_DIRECTORY", self.plan_dir):
            require_verified.return_value = TargetClassification(
                "real_suspected",
                "PX4 looked like hardware.",
            )
            with self.assertRaises(RuntimeError) as context:
                execute_reviewed_plan(self.plan_name)
            self.assertIn("real_suspected", str(context.exception))
            require_verified.assert_called_once()
            popen.assert_not_called()

    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    @patch("apps.gateway.plan_execution.subprocess.Popen")
    @patch("apps.gateway.plan_execution.wait_for_free_link", return_value=True)
    @patch("apps.gateway.plan_execution.TargetClassifier.require_sitl_verified")
    @patch("apps.gateway.plan_execution._validate_plan_before_spawn")
    def test_rechecks_classifier_on_every_approve(self, _validate, require_verified, _wait, popen) -> None:
        with patch.object(plan_execution, "_DEFAULT_PLAN_DIRECTORY", self.plan_dir):
            require_verified.side_effect = [
                TargetClassification("sitl_verified", "ok", {"status": "verified"}),
                TargetClassification("unknown", "Timed out reading SYS_AUTOSTART."),
            ]
            popen.return_value = MagicMock(pid=42, poll=MagicMock(return_value=None))
            execute_reviewed_plan(self.plan_name)
            with self.assertRaises(RuntimeError) as context:
                execute_reviewed_plan(self.plan_name)
            self.assertIn("unknown", str(context.exception))
            self.assertEqual(require_verified.call_count, 2)


if __name__ == "__main__":
    unittest.main()