"""Command endpoints stay unregistered until BYTEWOLF_COMMANDS_ENABLED is true."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import create_app
from shared.safety.commands_gate import commands_enabled, capabilities_document
from tests.command_approval_helpers import IDENTITY_TEST_ENV


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


def _telemetry_client(*, requests_flight: bool = True) -> TestClient:
    directory = TemporaryDirectory()
    telemetry = Path(directory.name) / "telemetry.json"
    telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
    gateway = DashboardCommandGateway(
        converse=lambda _session, _text: AgentReply("Terv", requests_flight),
        review=lambda _text: "plan-commands",
        execute=lambda plan: plan,
    )
    client = TestClient(create_app(telemetry, gateway=gateway))
    client._directory = directory  # type: ignore[attr-defined]
    return client


class CommandsGateUnitTests(unittest.TestCase):
    def test_commands_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(commands_enabled())

    def test_only_the_exact_string_true_enables_commands(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_COMMANDS_ENABLED": "true"}):
            self.assertTrue(commands_enabled())
        for value in (None, "", "True", " true ", "TRUE", "1", "yes", "false", "maybe"):
            with self.subTest(value=value):
                env = {} if value is None else {"BYTEWOLF_COMMANDS_ENABLED": value}
                with patch.dict(os.environ, env, clear=True):
                    self.assertFalse(commands_enabled())

    def test_capabilities_document_omits_target_class_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            payload = capabilities_document()
        self.assertEqual(payload["mode"], "ground")
        self.assertFalse(payload["commands_enabled"])
        self.assertNotIn("target_class", payload)


class CommandsGateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            IDENTITY_TEST_ENV,
            clear=True,
        )
        self._env_patch.start()
        self.client = _telemetry_client()

    def tearDown(self) -> None:
        patch_stop = getattr(self, "_env_patch", None)
        if patch_stop is not None:
            patch_stop.stop()
        client = getattr(self, "client", None)
        directory = getattr(client, "_directory", None) if client is not None else None
        if directory is not None:
            directory.cleanup()

    def test_capabilities_reports_ground_mode_by_default(self) -> None:
        response = self.client.get("/api/v1/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"mode": "ground", "commands_enabled": False})

    @patch.dict(os.environ, {"BYTEWOLF_COMMANDS_ENABLED": "true"}, clear=False)
    def test_capabilities_reports_sitl_console_when_enabled(self) -> None:
        client = _telemetry_client()
        response = client.get("/api/v1/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "sitl_console")
        self.assertTrue(response.json()["commands_enabled"])
        client._directory.cleanup()

    @patch.dict(os.environ, IDENTITY_TEST_ENV, clear=True)
    def test_command_routes_are_absent_when_disabled(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        body = {"north_m": 5.0, "east_m": 0.0, "altitude_m": 3.0, "goal": "Demo pont."}
        survey = {
            "centre_north_m": 0.0,
            "centre_east_m": 0.0,
            "radius_m": 4.0,
            "spacing_m": 2.0,
            "altitude_m": 3.0,
            "goal": "Felderítés.",
        }
        routes = [
            ("post", "/api/v1/plans/approve", {"plan_id": "plan-x"}),
            ("post", "/api/v1/plans/approval-token", {"plan_id": "plan-x"}),
            ("post", "/api/v1/plans/cancel", {"plan_id": "plan-x"}),
            ("post", "/api/v1/missions/point", body),
            ("post", "/api/v1/missions/survey", survey),
            ("post", "/api/v1/missions/demo-point-rtl", None),
            ("post", "/api/v1/missions/demo-waypoint-mission", None),
        ]
        for method, path, payload in routes:
            with self.subTest(path=path):
                kwargs = {"headers": headers}
                if payload is not None:
                    kwargs["json"] = payload
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404)

    @patch.dict(os.environ, IDENTITY_TEST_ENV, clear=True)
    def test_chat_flight_intent_returns_not_found_when_commands_disabled(self) -> None:
        response = self.client.post(
            "/api/v1/chat", headers={"X-ByteWolf-Session": SESSION}, json={"text": "repülj"}
        )
        self.assertEqual(response.status_code, 404)

    @patch.dict(os.environ, IDENTITY_TEST_ENV, clear=True)
    def test_chat_conversation_survives_when_commands_disabled(self) -> None:
        client = _telemetry_client(requests_flight=False)
        response = client.post(
            "/api/v1/chat", headers={"X-ByteWolf-Session": SESSION}, json={"text": "Szia"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "conversation")
        client._directory.cleanup()

    @patch.dict(os.environ, {"BYTEWOLF_COMMANDS_ENABLED": "true"}, clear=False)
    def test_command_routes_register_when_enabled(self) -> None:
        client = _telemetry_client()
        headers = {"X-ByteWolf-Session": SESSION}
        body = {"north_m": 5.0, "east_m": 0.0, "altitude_m": 3.0, "goal": "Demo pont."}
        proposed = client.post("/api/v1/chat", headers=headers, json={"text": "repülj"})
        self.assertEqual(proposed.status_code, 200)
        reviewed = client.post("/api/v1/missions/point", headers=headers, json=body)
        self.assertEqual(reviewed.status_code, 200)
        client._directory.cleanup()


if __name__ == "__main__":
    unittest.main()