"""BYTEWOLF_PX4_ENDPOINT validation at Control Room startup and spawn time."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.api.server import create_app
from apps.gateway.plan_execution import px4_endpoint_from_environment, validate_px4_endpoint_for_server
from brain.safety.connection_policy import ConnectionPolicyError


class PlanExecutionEndpointPolicyTests(unittest.TestCase):
    def test_default_endpoint_passes_server_validation(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BYTEWOLF_PX4_ENDPOINT", None)
            validate_px4_endpoint_for_server()

    def test_loopback_udpin_env_returns_canonical_endpoint(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://127.0.0.1:14541"}):
            self.assertEqual(px4_endpoint_from_environment(), "udpin://127.0.0.1:14541")

    def test_bind_all_env_fails_closed(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://0.0.0.0:14540"}):
            with self.assertRaises(ConnectionPolicyError):
                validate_px4_endpoint_for_server()

    def test_create_app_rejects_invalid_px4_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Path(directory) / "telemetry.json"
            telemetry.write_text(json.dumps({"schema_version": "dashboard-telemetry.v1"}), encoding="utf-8")
            with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://0.0.0.0:14540"}):
                with self.assertRaises(ConnectionPolicyError):
                    create_app(telemetry)


if __name__ == "__main__":
    unittest.main()