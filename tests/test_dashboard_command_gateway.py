"""The dashboard command path may propose missions, never bypass approval."""

import unittest

from apps.api.command_gateway import AgentReply, DashboardCommandGateway


class DashboardCommandGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reviewed: list[str] = []
        self.executed: list[str] = []
        self.gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Rendben, biztonságos tervet készítek.", True),
            review=lambda text: self.reviewed.append(text) or "plan-1",
            execute=lambda plan_id: self.executed.append(plan_id) or "started",
        )

    def test_chat_creates_a_pending_plan_but_does_not_execute(self) -> None:
        reply = self.gateway.chat("browser-1", "Szállj fel két méterre, majd szállj le.")

        self.assertEqual(self.reviewed, ["Szállj fel két méterre, majd szállj le."])
        self.assertEqual(self.executed, [])
        self.assertTrue(reply.approval_required)
        self.assertEqual(reply.plan_id, "plan-1")

    def test_only_the_same_session_can_approve_its_pending_plan(self) -> None:
        self.gateway.chat("browser-1", "repülj")

        with self.assertRaisesRegex(PermissionError, "No pending"):
            self.gateway.approve("browser-2", "plan-1")
        reply = self.gateway.approve("browser-1", "plan-1")

        self.assertEqual(self.executed, ["plan-1"])
        self.assertEqual(reply.status, "submitted")

    def test_cancel_removes_the_pending_plan(self) -> None:
        self.gateway.chat("browser-1", "repülj")
        self.gateway.cancel("browser-1", "plan-1")

        with self.assertRaisesRegex(PermissionError, "No pending"):
            self.gateway.approve("browser-1", "plan-1")


if __name__ == "__main__":
    unittest.main()
