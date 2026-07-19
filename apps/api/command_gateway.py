"""Session-scoped dashboard command gateway with explicit plan approval."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentReply:
    text: str
    requests_drone_action: bool = False


@dataclass(frozen=True)
class DashboardReply:
    text: str
    status: str
    plan_id: str | None = None
    approval_required: bool = False


Converse = Callable[[str, str], AgentReply]
Review = Callable[[str], str]
Execute = Callable[[str], str]


class DashboardCommandGateway:
    """Keep web/mobile conversation separate from the flight executor.

    This in-memory store intentionally holds only a pending plan ID.  The Pi
    runner owns the durable conversation and user-memory store, keyed by this
    same opaque browser session ID.
    """

    def __init__(self, *, converse: Converse, review: Review, execute: Execute) -> None:
        self._converse = converse
        self._review = review
        self._execute = execute
        self._pending_by_session: dict[str, str] = {}

    def chat(self, session_id: str, text: str) -> DashboardReply:
        if not text.strip():
            raise ValueError("Message cannot be empty.")
        reply = self._converse(session_id, text)
        if not reply.requests_drone_action:
            return DashboardReply(reply.text, "conversation")
        plan_id = self._review(text)
        self._pending_by_session[session_id] = plan_id
        return DashboardReply(
            f"{reply.text}\nElkészítettem a tervet. Indítsam a szimulációban?",
            "awaiting_approval",
            plan_id=plan_id,
            approval_required=True,
        )

    def approve(self, session_id: str, plan_id: str) -> DashboardReply:
        self._require_pending(session_id, plan_id)
        self._pending_by_session.pop(session_id)
        result = self._execute(plan_id)
        return DashboardReply(f"A küldetés {result}.", "submitted", plan_id=plan_id)

    def cancel(self, session_id: str, plan_id: str) -> DashboardReply:
        self._require_pending(session_id, plan_id)
        self._pending_by_session.pop(session_id)
        return DashboardReply("Rendben, nem indítok küldetést.", "cancelled", plan_id=plan_id)

    def _require_pending(self, session_id: str, plan_id: str) -> None:
        if self._pending_by_session.get(session_id) != plan_id:
            raise PermissionError("No pending plan belongs to this session.")
