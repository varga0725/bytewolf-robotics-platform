"""Shared mission-audit persistence for command-line mission runs."""

from pathlib import Path
from uuid import uuid4

from brain.mission.artifacts import (
    MissionArtifactWriter,
    MissionAuditArtifact,
    MissionTelemetrySnapshot,
)
from brain.mission.execution import MissionExecution


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
