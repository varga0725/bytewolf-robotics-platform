"""Immutable, versioned mission audit artifacts and local persistence."""

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile

from brain.mission.execution import MissionEvent, MissionExecution


DEFAULT_MISSION_RUNS_DIRECTORY = Path(__file__).resolve().parents[2] / "var/mission-runs"
MISSION_AUDIT_ARTIFACT_VERSION = "v0.2"


@dataclass(frozen=True)
class MissionTelemetrySnapshot:
    """Immutable preflight telemetry evidence attached to one mission artifact."""

    captured_at: datetime
    navigation_ready: bool
    home_position_valid: bool
    global_position_valid: bool
    battery_percent: float | None

    def to_document(self) -> dict[str, object]:
        return {
            "battery_percent": self.battery_percent,
            "captured_at": _format_timestamp(self.captured_at),
            "global_position_valid": self.global_position_valid,
            "home_position_valid": self.home_position_valid,
            "navigation_ready": self.navigation_ready,
        }


@dataclass(frozen=True)
class MissionAuditArtifact:
    """A portable snapshot of the immutable execution audit for one mission run."""

    version: str
    run_id: str
    recorded_at: datetime
    events: tuple[MissionEvent, ...]
    safety_decision: str = "not-evaluated"
    outcome: str = "failed"
    failure_reason: str | None = None
    telemetry: MissionTelemetrySnapshot | None = None

    @classmethod
    def from_execution(
        cls,
        run_id: str,
        execution: MissionExecution,
        recorded_at: datetime | None = None,
        safety_decision: str = "not-evaluated",
        outcome: str = "failed",
        failure_reason: str | None = None,
        telemetry: MissionTelemetrySnapshot | None = None,
    ) -> "MissionAuditArtifact":
        """Capture an execution without retaining mutable storage state."""
        return cls(
            version=MISSION_AUDIT_ARTIFACT_VERSION,
            run_id=run_id,
            recorded_at=recorded_at or datetime.now(UTC),
            events=execution.events,
            safety_decision=safety_decision,
            outcome=outcome,
            failure_reason=failure_reason,
            telemetry=telemetry,
        )

    def to_document(self) -> dict[str, object]:
        """Return the stable JSON-compatible representation for this artifact version."""
        return {
            "events": [
                {"phase": event.phase.value, "timestamp": _format_timestamp(event.timestamp)}
                for event in self.events
            ],
            "recorded_at": _format_timestamp(self.recorded_at),
            "run_id": self.run_id,
            "safety_decision": self.safety_decision,
            "outcome": self.outcome,
            "failure_reason": self.failure_reason,
            "telemetry": self.telemetry.to_document() if self.telemetry else None,
            "version": self.version,
        }


@dataclass(frozen=True)
class MissionArtifactWriter:
    """Persist mission audit artifacts atomically under one dedicated directory."""

    directory: Path = DEFAULT_MISSION_RUNS_DIRECTORY

    def write(self, artifact: MissionAuditArtifact) -> Path:
        """Write one artifact atomically and return its stable local path."""
        filename = _artifact_filename(artifact.run_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        output_path = self.directory / filename
        payload = json.dumps(
            artifact.to_document(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.directory, prefix=".pending-", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(f"{payload}\n")

        temporary_path.replace(output_path)
        return output_path


def _artifact_filename(run_id: str) -> str:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Artifact run_id must be a non-empty filename without path components.")
    return f"{run_id}.json"


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("Artifact timestamps must be timezone-aware.")
    utc_timestamp = timestamp.astimezone(UTC)
    return utc_timestamp.isoformat().replace("+00:00", "Z")
