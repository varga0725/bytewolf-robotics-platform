"""Append-only command audit log."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.api.command_audit import CommandAuditLog


class CommandAuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "events.jsonl"
        self.audit = CommandAuditLog(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_appends_propose_with_required_fields(self) -> None:
        self.audit.append(
            event="propose",
            operator="local",
            plan_hash="abc",
            target_class="unknown",
            gate_results={"commands_enabled": True},
        )
        line = self.path.read_text(encoding="utf-8").strip()
        body = json.loads(line)
        self.assertEqual(body["event"], "propose")
        self.assertEqual(body["operator"], "local")
        self.assertEqual(body["plan_hash"], "abc")
        self.assertEqual(body["target_class"], "unknown")
        self.assertIn("recorded_at", body)

    def test_append_is_non_destructive(self) -> None:
        self.audit.append(
            event="propose",
            operator="local",
            plan_hash="one",
            target_class="unknown",
            gate_results={},
        )
        self.audit.append(
            event="approve",
            operator="local",
            plan_hash="one",
            target_class="sitl_verified",
            gate_results={"mission_execution": "submitted"},
            approved_by="local",
        )
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["event"], "approve")
        self.assertEqual(json.loads(lines[1])["approved_by"], "local")

    def test_keeps_readable_token_refusal_without_secret_values(self) -> None:
        self.audit.append(
            event="refusal",
            operator="local",
            plan_hash=None,
            target_class="unknown",
            gate_results={"reason": "approval_token", "detail": "Approval token expired"},
        )
        line = self.path.read_text(encoding="utf-8").strip()
        self.assertIn("Approval token expired", line)
        self.assertNotRegex(line, r'"[A-Za-z0-9_\-]{24,}"')

    def test_refuses_external_ip_addresses_in_gate_results(self) -> None:
        with self.assertRaises(ValueError):
            self.audit.append(
                event="refusal",
                operator="local",
                plan_hash=None,
                target_class="unknown",
                gate_results={"detail": "peer 203.0.113.10"},
            )


if __name__ == "__main__":
    unittest.main()