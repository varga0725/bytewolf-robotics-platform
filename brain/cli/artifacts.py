"""Shared mission-audit persistence for command-line mission runs."""

from pathlib import Path
from uuid import uuid4

from brain.mission.artifacts import (
    MissionArtifactWriter,
    MissionAuditArtifact,
    MissionTelemetrySnapshot,
)
from brain.mission.execution import MissionExecution
from brain.mission.artifacts import DEFAULT_MISSION_RUNS_DIRECTORY


def mandatory_telemetry_history_path(
    artifact_directory: Path | None, requested_path: Path | None
) -> Path:
    """Return a unique durable history path for every connected flight invocation."""
    if requested_path is not None:
        return requested_path
    directory = artifact_directory or DEFAULT_MISSION_RUNS_DIRECTORY
    return directory / "telemetry-history" / f"{uuid4()}.jsonl"


def recorded_execution(adapter: object, execution: MissionExecution) -> MissionExecution:
    """Recover the phase trail an adapter reached but could not return by raising."""
    if execution.events:
        return execution
    recorded = getattr(adapter, "execution", None)
    return recorded if isinstance(recorded, MissionExecution) else execution


def write_run_artifact(
    directory: Path | None,
    execution: MissionExecution,
    safety_decision: str,
    outcome: str,
    failure_reason: str | None,
    telemetry: MissionTelemetrySnapshot | None = None,
) -> Path:
    """Persist the audit trail collected for this invocation under a safe unique ID."""
    safe_telemetry = telemetry if isinstance(telemetry, MissionTelemetrySnapshot) else None
    artifact = MissionAuditArtifact.from_execution(
        str(uuid4()),
        execution,
        safety_decision=safety_decision,
        outcome=outcome,
        failure_reason=failure_reason,
        telemetry=safe_telemetry,
    )
    writer = MissionArtifactWriter() if directory is None else MissionArtifactWriter(directory)
    return writer.write(artifact)
