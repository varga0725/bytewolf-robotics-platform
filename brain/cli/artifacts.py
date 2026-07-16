"""Shared mission-audit persistence for command-line mission runs."""

from pathlib import Path
from uuid import uuid4

from brain.mission.artifacts import MissionArtifactWriter, MissionAuditArtifact
from brain.mission.execution import MissionExecution


def write_run_artifact(directory: Path | None, execution: MissionExecution) -> Path:
    """Persist the audit trail collected for this invocation under a safe unique ID."""
    artifact = MissionAuditArtifact.from_execution(str(uuid4()), execution)
    writer = MissionArtifactWriter() if directory is None else MissionArtifactWriter(directory)
    return writer.write(artifact)
