"""The Pi Agent as a Cognitive Runtime adapter.

This composes the runtime into the same safe turn the dashboard Pi provides: it
reads drone state through the read-only ``telemetry.read`` plugin, and its one
reserved tool, ``draft_flight_request``, routes a drafted MissionSpec through the
existing reviewed-plan path -- the same ``validate_and_compile_mission_spec``
(the SafetyGate's validation) the CLIs use -- and stops at a pending review.

A turn can read state and draft a flight for approval. It cannot fly one: the
draft handler never touches a flight adapter, never executes, and the runtime's
envelope always reports ``reached_actuation: false``. Full conversational parity
(durable memory, world briefing, vision) is delivered by the cognitive-hooks and
read-only-plugin milestones, not by re-implementing the Node harness here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import json

from apps.plugins.telemetry_read import register as register_telemetry
from brain.cognitive_runtime import CognitiveRuntime, Provider
from brain.mission_spec.reviewed_plan import approval_path
from brain.mission_spec.validation import (
    load_mission_safety_profile,
    validate_and_compile_mission_spec,
)
from brain.plugin_sdk import PluginRegistry, ToolPolicy, build_tool_policy, load_plugin_manifest


PI_MODEL_LABEL = "cognitive-runtime.pi-adapter"

_CONSUMER = {
    "contract_version": "v0.1",
    "plugin_id": "pi.agent",
    "version": "0.1.0",
    "name": "Pi Agent",
    "provides": [{"capability_id": "pi.agent.turn", "version": "v0.1", "access": "read"}],
    "requests": [{"capability_id": "telemetry.read", "version": "v0.1"}],
}


def build_flight_request_handler(
    twin_path: Path | str, pending_dir: Path | str
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A draft handler that validates a MissionSpec and files it for review.

    The model passes a MissionSpec under ``mission_spec``. The handler runs it
    through the same validation the flight CLIs use. A rejected mission returns
    its issues and writes nothing; an accepted mission is written as an
    *unapproved* plan artifact awaiting explicit human approval. Neither path
    executes anything, and crucially the handler never writes an approval record:
    the executor refuses a plan without a matching approval, so a model-drafted
    plan cannot fly until a human review produces that approval separately.
    """
    profile = load_mission_safety_profile(twin_path)
    pending = Path(pending_dir)

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        mission_spec = arguments.get("mission_spec")
        if not isinstance(mission_spec, dict):
            return {"status": "rejected", "issues": ["no mission_spec object was provided"]}
        report = validate_and_compile_mission_spec(mission_spec, profile)
        if not report.approved:
            return {
                "status": "rejected",
                "issues": [f"{'/'.join(str(p) for p in i.path)}: {i.message}" for i in report.issues],
            }
        pending.mkdir(parents=True, exist_ok=True)
        plan_path = pending / f"{mission_spec.get('mission_id', 'pending')}.mission-spec.json"
        # Write the plan only -- never an approval record. A pending plan with no
        # approval is exactly what the executor refuses to fly.
        plan_path.write_text(
            json.dumps(mission_spec, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "status": "pending_review",
            "plan_path": str(plan_path),
            "awaiting_approval_at": str(approval_path(plan_path)),
        }

    return handler


def build_pi_runtime(
    provider: Provider,
    telemetry_path: Path | str,
    twin_path: Path | str,
    pending_dir: Path | str,
    *,
    system_prompt: str | None = None,
) -> tuple[CognitiveRuntime, ToolPolicy]:
    """Wire the runtime with the read-only telemetry plugin and the draft handler."""
    registry = PluginRegistry()
    register_telemetry(registry, Path(telemetry_path))
    registry.start("telemetry.read")

    runtime = CognitiveRuntime(
        provider,
        registry,
        prompt_version="cognitive-runtime.pi.v0_1",
        system_prompt=system_prompt,
        flight_request_handler=build_flight_request_handler(twin_path, pending_dir),
    )
    policy = build_tool_policy(
        load_plugin_manifest(_CONSUMER), registry, allowlist={"telemetry.read"}
    )
    return runtime, policy
