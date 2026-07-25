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


class PendingPlanReplacementTests(unittest.TestCase):
    """One pending plan per session — replaced when needed, never in silence.

    A second proposal cannot make two plans approvable at once; that is the
    safety property. What it can do is leave the user looking at a plan the
    gateway has already dropped, so the reply names what lost its turn and the
    later refusal says why.
    """

    def _gateway(self) -> DashboardCommandGateway:
        self.executed: list[str] = []
        return DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Rendben.", False),
            review=lambda _text: "plan-x",
            execute=lambda plan: self.executed.append(plan) or "submitted",
        )

    def test_a_replacement_names_the_plan_it_displaced(self) -> None:
        gateway = self._gateway()

        gateway.propose("session", "plan-a", "Első terv.")
        reply = gateway.propose("session", "plan-b", "Második terv.")

        self.assertIn("plan-a", reply.text)
        self.assertEqual(reply.plan_id, "plan-b")

    def test_the_displaced_plan_is_refused_with_the_reason(self) -> None:
        gateway = self._gateway()
        gateway.propose("session", "plan-a", "Első terv.")
        gateway.propose("session", "plan-b", "Második terv.")

        with self.assertRaisesRegex(PermissionError, "newer mission replaced it"):
            gateway.approve("session", "plan-a")

        self.assertEqual(self.executed, [], "only the pending plan can ever fly")

    def test_proposing_the_same_plan_twice_says_nothing_extra(self) -> None:
        gateway = self._gateway()

        gateway.propose("session", "plan-a", "Terv.")
        reply = gateway.propose("session", "plan-a", "Terv.")

        self.assertEqual(reply.text, "Terv.")

    def test_a_session_with_no_pending_plan_still_says_so(self) -> None:
        gateway = self._gateway()

        with self.assertRaisesRegex(PermissionError, "No pending plan"):
            gateway.approve("session", "plan-a")

    def test_replacing_across_surfaces_keeps_one_slot(self) -> None:
        """A chat plan and a map plan compete for the same slot, visibly."""
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Tervet készítek.", True),
            review=lambda _text: "chat-plan",
            execute=lambda plan: "submitted",
        )
        gateway.propose("session", "map-plan", "Térkép-terv.")

        reply = gateway.chat("session", "repülj a piros jelhez")

        self.assertIn("map-plan", reply.text)
        self.assertEqual(reply.plan_id, "chat-plan")


if __name__ == "__main__":
    unittest.main()
