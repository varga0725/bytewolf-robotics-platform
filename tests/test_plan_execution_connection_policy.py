"""BYTEWOLF_PX4_ENDPOINT validation at Control Room startup and spawn time."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from apps.api.server import create_app
from apps.gateway.plan_execution import validate_px4_endpoint_for_server
from brain.safety.connection_policy import ConnectionPolicyError
from pathlib import Path


class PlanExecutionEndpointPolicyTests(unittest.TestCase):
    def test_default_endpoint_passes_server_validation(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BYTEWOLF_PX4_ENDPOINT", None)
            validate_px4_endpoint_for_server()

    def test_loopback_udpin_env_passes(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://127.0.0.1:14541"}):
            validate_px4_endpoint_for_server()

    def test_bind_all_env_fails_closed(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://0.0.0.0:14540"}):
            with self.assertRaises(ConnectionPolicyError):
                validate_px4_endpoint_for_server()

    def test_create_app_rejects_invalid_px4_endpoint(self) -> None:
        telemetry = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "missing-telemetry.json"
        if not telemetry.is_file():
            telemetry = Path(__file__).resolve().parents[1] / "missing-telemetry.json"
        with patch.dict(os.environ, {"BYTEWOLF_PX4_ENDPOINT": "udpin://0.0.0.0:14540"}):
            with self.assertRaises(ConnectionPolicyError):
                create_app(telemetry)


if __name__ == "__main__":
    unittest.main()