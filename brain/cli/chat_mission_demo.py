"""Walk one chat message through the whole system, up to a flight.

```
chat text ──> PiConversation (NIM + read-only plugins)
                    │  agent calls draft_flight_request: intent, not a plan
                    v
              CommandGateway.chat ──> review ──> MissionSpec + SafetyGate
                    │  (gate: a human approves this exact plan)
                    v
              CommandGateway.approve ──> fly_nim_mission --execute ──> PX4 SITL
```

The point is that none of this is new. It builds the *same*
``DashboardCommandGateway`` the web app builds, with the same conversation, the
same review step and the same execute step, so chat is one more way to **ask**
and never a second way to **fly**. A demo that reached PX4 by any other route
would prove something about the demo rather than about the system.

Two things it stops for. The agent cannot author a MissionSpec and is not asked
to: ``draft_flight_request`` records that the user wants to fly, and the
gateway's review step is what compiles a request into a plan the SafetyGate has
validated. That plan then sits pending until a person approves it by name --
``--approve`` here, a button in the dashboard. Without the flag this prints the
plan it would have flown and exits.

**What this does not have: a runtime shield.** The mission path flies
``goto_location`` in Auto/Hold, and a capability probe measured what that means
-- PX4 Collision Prevention runs only in Position mode, and the vehicle reached
0.36 m from an obstacle with CP enabled and no intervention. The geometric
shield built in this phase guards the *Offboard* stream, which this path does
not use. So this demo exercises the cognitive chain end to end, in an empty
world, and claims nothing about obstacle safety.

Example::

    python -m brain.cli.chat_mission_demo --text "Szállj fel két méterre és gyere vissza."
    python -m brain.cli.chat_mission_demo --text "..." --approve
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import uuid


@dataclass(frozen=True)
class ChatFlightRecord:
    """What one chat-to-flight attempt did, and what it is allowed to claim."""

    recorded_at: str
    session_id: str
    request: str
    reply: str
    status: str
    plan_id: str | None
    approval_required: bool
    approved: bool
    executed: bool
    execution_status: str
    #: Stated in the artifact rather than left to a reader's assumption.
    caveats: list[str] = field(default_factory=list)
    proof_level: str = "app+SITL"

    def as_document(self) -> dict:
        return asdict(self)


CAVEATS = [
    "No runtime safety shield is on this path: the mission route flies goto_location "
    "in Auto/Hold, and the geometric shield guards the Offboard stream instead.",
    "PX4 Collision Prevention does not protect this route either; a capability probe "
    "measured 0.36 m clearance with CP enabled and no intervention.",
    "Flown in an empty world, so nothing here is a claim about obstacle safety.",
    "SITL is not an aircraft, and the twin still has no measured physical parameters.",
]


def _gateway(artifact_root: Path):
    """Build exactly the gateway the web app builds, with the live conversation."""
    from apps.api.command_gateway import DashboardCommandGateway
    from apps.api.server import _build_live_converse, _capability_briefing
    from apps.gateway.telegram_mission_gateway import _execute_with_cli, _review_with_cli

    twin_path = Path("shared/config/x500v2/twin.yaml")
    return DashboardCommandGateway(
        converse=_build_live_converse(
            Path("simulation/artifacts/dashboard/live-telemetry.json"),
            Path("simulation/artifacts/dashboard/detections.json"),
            twin_path,
            Path("var/world-memory"),
            artifact_root / "memory",
            _capability_briefing(twin_path),
            None,
        ),
        review=_review_with_cli,
        execute=_execute_with_cli,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text", required=True, help="What the operator types into chat.")
    parser.add_argument(
        "--approve", action="store_true",
        help="Approve the reviewed plan and fly it. Without this the run stops at the plan.",
    )
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("simulation/artifacts/chat-missions"),
    )
    arguments = parser.parse_args(argv)

    arguments.artifact_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"demo-{uuid.uuid4().hex[:8]}"
    gateway = _gateway(arguments.artifact_dir)

    print(f"chat> {arguments.text}")
    reply = gateway.chat(session_id, arguments.text)
    print(f"agent> {reply.text}")
    print(f"  status={reply.status} plan={reply.plan_id} approval_required={reply.approval_required}")

    approved = False
    executed = False
    execution_status = "not_attempted"
    if reply.approval_required and reply.plan_id:
        if not arguments.approve:
            print(
                "\nStopping here. A person has to approve this exact plan before anything "
                "flies; re-run with --approve to do that."
            )
            execution_status = "awaiting_human_approval"
        else:
            print(f"\napproving {reply.plan_id} and executing…")
            approved = True
            outcome = gateway.approve(session_id, reply.plan_id)
            print(f"agent> {outcome.text}")
            execution_status = outcome.status
            executed = outcome.status not in {"rejected", "error"}

    record = ChatFlightRecord(
        recorded_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        session_id=session_id,
        request=arguments.text,
        reply=reply.text,
        status=reply.status,
        plan_id=reply.plan_id,
        approval_required=reply.approval_required,
        approved=approved,
        executed=executed,
        execution_status=execution_status,
        caveats=list(CAVEATS),
    )
    destination = arguments.artifact_dir / f"chat-mission-{_stamp()}.json"
    destination.write_text(json.dumps(record.as_document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nevidence: {destination}")
    return 0


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    sys.exit(main())
