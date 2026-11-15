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

    def test_only_explicit_true_enables_commands(self) -> None:
        for value in ("TRUE", " true "):
            with self.subTest(value=value), patch.dict(os.environ, {"BYTEWOLF_COMMANDS_ENABLED": value}):
                self.assertTrue(commands_enabled())
        for value in ("", "1", "yes", "false", "maybe"):
            with self.subTest(value=value), patch.dict(os.environ, {"BYTEWOLF_COMMANDS_ENABLED": value}):
                self.assertFalse(commands_enabled())

    def test_capabilities_document_omits_target_class_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            payload = capabilities_document()
        self.assertEqual(payload["mode"], "ground")
        self.assertFalse(payload["commands_enabled"])
        self.assertNotIn("target_class", payload)


class CommandsGateApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        client = getattr(self, "client", None)
        directory = getattr(client, "_directory", None) if client is not None else None
        if directory is not None:
            directory.cleanup()

    @patch.dict(os.environ, {}, clear=True)
    def setUp(self) -> None:
        self.client = _telemetry_client()

    @patch.dict(os.environ, {}, clear=True)
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

    @patch.dict(os.environ, {}, clear=True)
    def test_command_routes_are_absent_when_disabled(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        body = {"north_m": 5.0, "east_m": 0.0, "altitude_m": 3.0, "goal": "Demo pont."}
        routes = [
            ("post", "/api/v1/plans/approve", {"plan_id": "plan-x"}),
            ("post", "/api/v1/plans/cancel", {"plan_id": "plan-x"}),
            ("post", "/api/v1/missions/point", body),
        ]
        for method, path, payload in routes:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, headers=headers, json=payload)
                self.assertEqual(response.status_code, 404)

    @patch.dict(os.environ, {}, clear=True)
    def test_chat_flight_intent_returns_not_found_when_commands_disabled(self) -> None:
        response = self.client.post(
            "/api/v1/chat", headers={"X-ByteWolf-Session": SESSION}, json={"text": "repülj"}
        )
        self.assertEqual(response.status_code, 404)

    @patch.dict(os.environ, {}, clear=True)
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