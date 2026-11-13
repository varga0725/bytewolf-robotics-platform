"""Shared mission-audit persistence for command-line mission runs."""

from pathlib import Path
import os
from math import isfinite
from uuid import uuid4
from dataclasses import dataclass

from brain.mission.artifacts import (
    MissionArtifactWriter,
    MissionAuditArtifact,
    MissionTelemetrySnapshot,
)
from brain.memory.recorder import WorldMemoryRecorder
from brain.mission.execution import MissionExecution
from brain.mission.artifacts import DEFAULT_MISSION_RUNS_DIRECTORY
from brain.telemetry.ulog import (
    archive_px4_ulog,
    discover_newest_px4_ulog,
    ensure_px4_ulog_archived,
    read_ulog_manifest,
    resolve_px4_run_directory,
    write_ulog_unavailable_manifest,
    ULogCaptureError,
)


@dataclass(frozen=True)
class FlightRunRecording:
    """One immutable identifier shared by audit and mandatory telemetry evidence."""

    run_id: str
    telemetry_history_path: Path


def prepare_flight_run_recording(
    artifact_directory: Path | None, requested_history_path: Path | None
) -> FlightRunRecording:
    """Allocate an auditable identity before the flight path can start."""
    run_id = str(uuid4())
    directory = artifact_directory or DEFAULT_MISSION_RUNS_DIRECTORY
    history_path = requested_history_path or directory / "telemetry-history" / f"{run_id}.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(history_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError(f"Telemetry history destination '{history_path}' already exists.") from error
    os.close(descriptor)
    return FlightRunRecording(run_id, history_path)


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
    run_id: str | None = None,
    px4_ulog: Path | None = None,
    peak_relative_altitude_m: float | None = None,
    world_recorder: WorldMemoryRecorder | None = None,
) -> Path:
    """Persist the audit trail collected for this invocation under a safe unique ID.

    The audit artifact is the authoritative record and is written first. World
    memory is a derived, perishable summary of the same run: it is recorded
    afterwards and only when a recorder is supplied, so a memory-write problem
    can never cost the run its audit trail.
    """
    safe_telemetry = telemetry if isinstance(telemetry, MissionTelemetrySnapshot) else None
    safe_peak: float | None = None
    if isinstance(peak_relative_altitude_m, (int, float)) and isfinite(float(peak_relative_altitude_m)):
        safe_peak = float(peak_relative_altitude_m)
    artifact = MissionAuditArtifact.from_execution(
        run_id or str(uuid4()),
        execution,
        safety_decision=safety_decision,
        outcome=outcome,
        failure_reason=failure_reason,
        telemetry=safe_telemetry,
        peak_relative_altitude_m=safe_peak,
    )
    writer = MissionArtifactWriter() if directory is None else MissionArtifactWriter(directory)
    artifact_path = writer.write(artifact)
    manifest = read_ulog_manifest(writer.directory, artifact.run_id)
    if manifest is not None and manifest.get("status") == "captured":
        return artifact_path
    if px4_ulog is None:
        run_dir = resolve_px4_run_directory()
        if run_dir is not None:
            px4_ulog = discover_newest_px4_ulog(run_dir)
    if px4_ulog is None:
        reason = (
            "No PX4 ULog source was supplied."
            if resolve_px4_run_directory() is None
            else "No .ulg file was found under BYTEWOLF_PX4_RUN_DIR after the mission."
        )
        if manifest is None or manifest.get("status") != "unavailable":
            write_ulog_unavailable_manifest(writer.directory, artifact.run_id, reason)
    else:
        try:
            archive_px4_ulog(px4_ulog, writer.directory, artifact.run_id)
        except ULogCaptureError:
            manifest = read_ulog_manifest(writer.directory, artifact.run_id)
            if manifest is None or manifest.get("status") != "captured":
                raise
    if world_recorder is not None:
        world_recorder.record_mission_outcome(artifact, artifact_path=str(artifact_path))
    return artifact_path
