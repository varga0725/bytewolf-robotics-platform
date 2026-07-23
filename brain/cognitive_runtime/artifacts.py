"""Persist a response envelope as a durable, structured audit artifact.

Every turn's envelope can be written to disk the way a MissionExecution is: one
versioned JSON file per turn, carrying the model, prompt version, latency, token
usage, the tool trace and the safety verdict. The write is atomic (temp file then
rename), so a reader never sees a half-written artifact.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path

from brain.cognitive_runtime.contracts import ResponseEnvelope


ARTIFACT_VERSION = "v0.1"


def envelope_to_dict(envelope: ResponseEnvelope) -> dict:
    """Reconstruct the schema-shaped envelope document from the dataclass."""
    document = {
        "contract_version": "v0.1",
        "session_id": envelope.session_id,
        "turn_id": envelope.turn_id,
        "status": envelope.status,
        "model": envelope.model,
        "prompt_version": envelope.prompt_version,
        "reply": envelope.reply,
        "latency_ms": envelope.latency_ms,
        "tool_trace": [
            {k: v for k, v in asdict(entry).items() if v is not None}
            for entry in envelope.tool_trace
        ],
        "safety_verdict": dict(envelope.safety_verdict),
    }
    if envelope.provider is not None:
        document["provider"] = envelope.provider
    if envelope.token_usage is not None:
        document["token_usage"] = dict(envelope.token_usage)
    if envelope.error is not None:
        document["error"] = dict(envelope.error)
    return document


def persist_envelope(envelope: ResponseEnvelope, artifact_dir: Path) -> Path:
    """Write one turn's envelope as ``<turn_id>.json`` and return its path."""
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {"artifact_version": ARTIFACT_VERSION, "envelope": envelope_to_dict(envelope)}
    destination = artifact_dir / f"{envelope.turn_id}.json"
    tmp = destination.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, destination)
    return destination
