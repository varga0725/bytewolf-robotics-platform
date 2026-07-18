"""Read mission audit artifacts offline without creating a flight-control client."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from brain.mission.execution import MissionEvent, MissionExecution, MissionPhase, MissionTransitionError
from brain.telemetry.persistence import TelemetryHistoryEvent, load_telemetry_history


SUPPORTED_ARTIFACT_VERSIONS = frozenset({"v0.2"})


class MissionReplayError(ValueError):
    """Raised when a persisted mission artifact cannot be replayed safely."""


@dataclass(frozen=True)
class MissionReplay:
    """Immutable, read-only interpretation of one mission artifact."""

    run_id: str
    recorded_at: datetime
    safety_decision: str
    outcome: str
    failure_reason: str | None
    events: tuple[MissionEvent, ...]
    preflight_battery_percent: float | None
    preflight_navigation_ready: bool | None
    preflight_home_position_valid: bool | None
    preflight_global_position_valid: bool | None
    telemetry_events: tuple[TelemetryHistoryEvent, ...] = ()

    @property
    def terminal_phase(self) -> MissionPhase | None:
        """Return the recorded terminal phase, never issuing a control command."""
        return self.events[-1].phase if self.events else None


def replay_artifact(path: Path) -> MissionReplay:
    """Load one locally persisted artifact for offline analysis only."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise MissionReplayError(f"Cannot read replay artifact '{path}': {error.strerror}.") from error
    except json.JSONDecodeError as error:
        raise MissionReplayError(f"Replay artifact '{path}' is not valid JSON.") from error
    return replay_document(document)


def replay_run(artifact_path: Path, telemetry_history_path: Path | None = None) -> MissionReplay:
    """Join an audit artifact to the same run's append-only telemetry history offline."""
    replay = replay_artifact(artifact_path)
    history_path = telemetry_history_path or (
        artifact_path.parent / "telemetry-history" / f"{replay.run_id}.jsonl"
    )
    try:
        telemetry_events = load_telemetry_history(history_path, expected_run_id=replay.run_id)
    except ValueError as error:
        raise MissionReplayError(f"Replay telemetry history is invalid: {error}") from error
    return replace(replay, telemetry_events=telemetry_events)


def replay_document(document: object) -> MissionReplay:
    """Validate an artifact document and expose its recorded timeline immutably."""
    if not isinstance(document, dict):
        raise MissionReplayError("Replay artifact root must be an object.")
    version = _required_string(document, "version")
    if version not in SUPPORTED_ARTIFACT_VERSIONS:
        raise MissionReplayError(f"Replay artifact version '{version}' is unsupported.")

    events = _load_events(document.get("events"))
    telemetry = _load_telemetry(document.get("telemetry"))
    failure_reason = document.get("failure_reason")
    if failure_reason is not None and not isinstance(failure_reason, str):
        raise MissionReplayError("Replay artifact failure_reason must be a string or null.")

    return MissionReplay(
        run_id=_required_string(document, "run_id"),
        recorded_at=_timestamp(_required_string(document, "recorded_at"), "recorded_at"),
        safety_decision=_required_string(document, "safety_decision"),
        outcome=_required_string(document, "outcome"),
        failure_reason=failure_reason,
        events=events,
        preflight_battery_percent=telemetry.battery_percent if telemetry else None,
        preflight_navigation_ready=telemetry.navigation_ready if telemetry else None,
        preflight_home_position_valid=telemetry.home_position_valid if telemetry else None,
        preflight_global_position_valid=telemetry.global_position_valid if telemetry else None,
    )


def _load_events(value: object) -> tuple[MissionEvent, ...]:
    if not isinstance(value, list):
        raise MissionReplayError("Replay artifact events must be a list.")
    events: list[MissionEvent] = []
    execution = MissionExecution.empty()
    previous_at: datetime | None = None
    for index, raw_event in enumerate(value):
        if not isinstance(raw_event, dict):
            raise MissionReplayError(f"Replay event {index} must be an object.")
        try:
            phase = MissionPhase(_required_string(raw_event, "phase"))
        except ValueError as error:
            raise MissionReplayError(f"Replay event {index} has an unknown phase.") from error
        timestamp = _timestamp(_required_string(raw_event, "timestamp"), f"events[{index}].timestamp")
        if previous_at is not None and timestamp < previous_at:
            raise MissionReplayError("Replay artifact events are out of chronological order.")
        try:
            execution = execution.transition(phase, timestamp)
        except MissionTransitionError as error:
            raise MissionReplayError(f"Replay event {index} violates the mission state machine.") from error
        events.append(MissionEvent(phase=phase, timestamp=timestamp))
        previous_at = timestamp
    return tuple(events)


@dataclass(frozen=True)
class _PreflightTelemetry:
    battery_percent: float | None
    navigation_ready: bool
    home_position_valid: bool
    global_position_valid: bool


def _load_telemetry(value: object) -> _PreflightTelemetry | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MissionReplayError("Replay artifact telemetry must be an object or null.")
    _timestamp(_required_string(value, "captured_at"), "telemetry.captured_at")
    navigation_ready = _required_boolean(value, "navigation_ready")
    home_position_valid = _required_boolean(value, "home_position_valid")
    global_position_valid = _required_boolean(value, "global_position_valid")
    battery = value.get("battery_percent")
    if battery is None:
        return _PreflightTelemetry(None, navigation_ready, home_position_valid, global_position_valid)
    if type(battery) not in (int, float) or not 0.0 <= float(battery) <= 100.0:
        raise MissionReplayError("Replay telemetry battery_percent must be between 0.0 and 100.0.")
    return _PreflightTelemetry(
        float(battery), navigation_ready, home_position_valid, global_position_valid
    )


def _required_string(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise MissionReplayError(f"Replay artifact {field} must be a non-empty string.")
    return value


def _required_boolean(document: dict[str, Any], field: str) -> bool:
    value = document.get(field)
    if type(value) is not bool:
        raise MissionReplayError(f"Replay artifact telemetry {field} must be a boolean.")
    return value


def _timestamp(value: str, field: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MissionReplayError(f"Replay artifact {field} must be RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise MissionReplayError(f"Replay artifact {field} must include an offset.")
    return timestamp.astimezone(UTC)
