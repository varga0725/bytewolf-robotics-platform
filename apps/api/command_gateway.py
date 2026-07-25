"""Session-scoped dashboard command gateway with explicit plan approval."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


MEMORY_UPDATE_STATES = frozenset({"updated", "skipped", "unavailable"})


def _superseded_note(superseded: str | None) -> str:
    if superseded is None:
        return ""
    return f"\n(A korábbi, jóvá nem hagyott terv — {superseded} — ezzel érvényét vesztette.)"


@dataclass(frozen=True)
class AgentReply:
    text: str
    requests_drone_action: bool = False
    memory_update: str = "unavailable"


@dataclass(frozen=True)
class DashboardReply:
    text: str
    status: str
    plan_id: str | None = None
    approval_required: bool = False
    # Diagnostics only: whether the isolated post-turn hook stored anything.
    # It never carries a remembered fact, and it never gates the reply.
    memory_update: str = "unavailable"


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
        memory_update = reply.memory_update if reply.memory_update in MEMORY_UPDATE_STATES else "unavailable"
        if not reply.requests_drone_action:
            return DashboardReply(reply.text, "conversation", memory_update=memory_update)
        plan_id = self._review(text)
        superseded = self._set_pending(session_id, plan_id)
        return DashboardReply(
            f"{reply.text}\nElkészítettem a tervet. Indítsam a szimulációban?{_superseded_note(superseded)}",
            "awaiting_approval",
            plan_id=plan_id,
            approval_required=True,
            memory_update=memory_update,
        )

    def propose(self, session_id: str, plan_id: str, text: str) -> DashboardReply:
        """Register an already-reviewed plan as this session's pending mission.

        The map page reviews its own plan — a picked point needs no language
        model — but the approval boundary must stay exactly the same one the
        chat path uses, so the plan enters through this single pending slot and
        leaves only through approve or cancel.
        """
        if not plan_id.strip():
            raise ValueError("A proposed mission needs a plan.")
        superseded = self._set_pending(session_id, plan_id)
        return DashboardReply(
            f"{text}{_superseded_note(superseded)}",
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

    def _set_pending(self, session_id: str, plan_id: str) -> str | None:
        """Hold exactly one pending plan, and say which one it displaced.

        One slot per session is the safety property: a second proposal can never
        make two plans approvable at once. What it *can* do is leave the user
        looking at a plan the gateway has already dropped, so replacing is
        allowed but never silent — the reply names the plan that lost its turn.
        """
        superseded = self._pending_by_session.get(session_id)
        self._pending_by_session[session_id] = plan_id
        return superseded if superseded != plan_id else None

    def _require_pending(self, session_id: str, plan_id: str) -> None:
        pending = self._pending_by_session.get(session_id)
        if pending == plan_id:
            return
        if pending is None:
            raise PermissionError("No pending plan belongs to this session.")
        raise PermissionError(
            "This plan is no longer the pending one; a newer mission replaced it. "
            "Review it again before approving."
        )
