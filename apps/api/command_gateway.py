"""Session-scoped dashboard command gateway with explicit plan approval."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading


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
    plan_hash: str | None = None
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
        self._lock = threading.Lock()
        self._pending_by_session: dict[str, tuple[str, str]] = {}

    def chat(self, session_id: str, text: str) -> DashboardReply:
        if not text.strip():
            raise ValueError("Message cannot be empty.")
        reply = self._converse(session_id, text)
        memory_update = reply.memory_update if reply.memory_update in MEMORY_UPDATE_STATES else "unavailable"
        if not reply.requests_drone_action:
            return DashboardReply(reply.text, "conversation", memory_update=memory_update)
        from shared.safety.commands_gate import require_commands_enabled

        require_commands_enabled()
        plan_id = self._review(text)
        superseded = self._set_pending(session_id, plan_id, plan_hash="")
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
        superseded = self._set_pending(session_id, plan_id, plan_hash="")
        return DashboardReply(
            f"{text}{_superseded_note(superseded)}",
            "awaiting_approval",
            plan_id=plan_id,
            approval_required=True,
        )

    def pending_plan(self, session_id: str) -> str | None:
        entry = self._pending_by_session.get(session_id)
        return None if entry is None else entry[0]

    def pending_plan_hash(self, session_id: str) -> str | None:
        entry = self._pending_by_session.get(session_id)
        if entry is None:
            return None
        plan_hash = entry[1]
        return plan_hash or None

    def attach_plan_hash(self, session_id: str, plan_id: str, plan_hash: str) -> None:
        with self._lock:
            entry = self._pending_by_session.get(session_id)
            if entry is None or entry[0] != plan_id:
                return
            self._pending_by_session[session_id] = (plan_id, plan_hash)

    def ensure_pending(self, session_id: str, plan_id: str) -> None:
        self._require_pending(session_id, plan_id)

    def ensure_pending_plan_hash(self, session_id: str, plan_id: str) -> str:
        self._require_pending(session_id, plan_id)
        stored = self.pending_plan_hash(session_id)
        if not stored:
            raise PermissionError("Pending plan hash is not recorded.")
        return stored

    def approve(self, session_id: str, plan_id: str, *, approved_by: str) -> DashboardReply:
        from shared.safety.execution_gate import require_mission_execution_enabled

        if not approved_by.strip():
            raise PermissionError("Human approval requires a server-issued token.")
        with self._lock:
            entry = self._pending_by_session.get(session_id)
            if entry is None or entry[0] != plan_id:
                if entry is None:
                    raise PermissionError("No pending plan belongs to this session.")
                raise PermissionError(
                    "This plan is no longer the pending one; a newer mission replaced it. "
                    "Review it again before approving."
                )
        require_mission_execution_enabled()
        with self._lock:
            popped = self._pending_by_session.pop(session_id, None)
            if popped is None or popped[0] != plan_id:
                raise PermissionError("No pending plan belongs to this session.")
        result = self._execute(plan_id)
        return DashboardReply(f"A küldetés {result}.", "submitted", plan_id=plan_id)

    def cancel(self, session_id: str, plan_id: str) -> DashboardReply:
        with self._lock:
            self._require_pending(session_id, plan_id)
            self._pending_by_session.pop(session_id, None)
        return DashboardReply("Rendben, nem indítok küldetést.", "cancelled", plan_id=plan_id)

    def _set_pending(self, session_id: str, plan_id: str, plan_hash: str) -> str | None:
        with self._lock:
            superseded_entry = self._pending_by_session.get(session_id)
            superseded = None if superseded_entry is None else superseded_entry[0]
            self._pending_by_session[session_id] = (plan_id, plan_hash)
            return superseded if superseded != plan_id else None

    def _require_pending(self, session_id: str, plan_id: str) -> None:
        entry = self._pending_by_session.get(session_id)
        if entry is not None and entry[0] == plan_id:
            return
        if entry is None:
            raise PermissionError("No pending plan belongs to this session.")
        raise PermissionError(
            "This plan is no longer the pending one; a newer mission replaced it. "
            "Review it again before approving."
        )