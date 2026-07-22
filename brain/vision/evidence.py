"""Immutable P0 evidence contracts and local retention enforcement.

This module is observation-only.  It intentionally has no video codec,
transport, or flight-control dependency. Deployments may supply any compliant
``EncryptedEvidenceWriter``; the optional Fernet writer below is a local P0
implementation with caller-provisioned key material.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

import yaml


DEFAULT_EVIDENCE_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "shared/config/vision/evidence.v1.yaml"
)
EVIDENCE_POLICY_VERSION = "v1"


class EvidencePolicyError(ValueError):
    """Raised when the versioned evidence policy is incomplete or unsafe."""


class EvidenceEncryptionError(RuntimeError):
    """Evidence encryption cannot proceed safely with the local runtime/key."""


class EncryptedEvidenceWriter(Protocol):
    """Deployment-provided writer responsible for encrypted payload persistence."""

    def write_encrypted(self, target: Path, payload: bytes) -> None:
        """Persist payload at target using the deployment's encryption boundary."""


class FernetEvidenceWriter:
    """Locally encrypt evidence with authenticated Fernet tokens.

    The key must be injected by the deployment; this class never generates,
    stores, logs, or returns key material. ``cryptography`` is imported only
    when the writer is requested, keeping non-evidence Vision deployments free
    of this optional dependency.
    """

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or not key:
            raise EvidenceEncryptionError("Evidence encryption requires a non-empty Fernet key.")
        try:
            from cryptography.fernet import Fernet
        except ImportError as error:  # pragma: no cover - deployment guard
            raise EvidenceEncryptionError(
                "Fernet evidence encryption requires the approved cryptography runtime."
            ) from error
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as error:
            raise EvidenceEncryptionError("Evidence encryption key is not a valid Fernet key.") from error

    @classmethod
    def from_environment(cls, variable: str = "BYTEWOLF_VISION_EVIDENCE_KEY") -> FernetEvidenceWriter:
        """Load an explicitly named deployment secret without printing it."""
        if not isinstance(variable, str) or not variable:
            raise EvidenceEncryptionError("Evidence encryption environment variable name is required.")
        value = os.environ.get(variable)
        if value is None:
            raise EvidenceEncryptionError(f"Evidence encryption key is missing from {variable}.")
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as error:
            raise EvidenceEncryptionError("Evidence encryption key must be ASCII Fernet key material.") from error
        return cls(encoded)

    def write_encrypted(self, target: Path, payload: bytes) -> None:
        """Atomically persist an authenticated encrypted payload with mode 0600."""
        if not isinstance(target, Path) or not target.name:
            raise EvidenceEncryptionError("Evidence encryption target must be a concrete file path.")
        if not isinstance(payload, bytes):
            raise TypeError("Evidence encryption payload must be bytes.")
        if not target.parent.is_dir():
            raise EvidenceEncryptionError("Evidence encryption target directory must already exist.")
        token = self._fernet.encrypt(payload)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(token)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
            os.chmod(target, 0o600)
        except OSError as error:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise EvidenceEncryptionError(f"Cannot persist encrypted evidence: {error}") from error


@dataclass(frozen=True)
class EvidencePolicy:
    """Versioned, metadata-first recording policy."""

    version: str
    default_mode: str
    evidence_clip_enabled: bool
    pre_event_seconds: int
    post_event_seconds: int
    retention_days: int
    full_session_recording_enabled: bool


@dataclass(frozen=True)
class EvidenceEvent:
    """Immutable observation that may justify retaining a short evidence clip."""

    event_id: str
    occurred_at: datetime
    stream_session_id: str
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        _validate_identifier(self.event_id, "event_id")
        _validate_identifier(self.stream_session_id, "stream_session_id")
        _require_aware(self.occurred_at, "occurred_at")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class FrameReference:
    """Reference to a captured frame; pixel bytes deliberately stay elsewhere."""

    frame_sequence: int
    captured_at: datetime
    stream_session_id: str

    def __post_init__(self) -> None:
        if self.frame_sequence < 0:
            raise ValueError("frame_sequence must be non-negative")
        _validate_identifier(self.stream_session_id, "stream_session_id")
        _require_aware(self.captured_at, "captured_at")


@dataclass(frozen=True)
class EvidenceClipPlan:
    """Deterministic frame selection plan, not a recording command."""

    event: EvidenceEvent
    start_at: datetime
    end_at: datetime
    frame_sequences: tuple[int, ...]
    retention_deadline: datetime


@dataclass(frozen=True)
class EvidenceRecord:
    """Metadata linking an evidence event to a local encrypted payload path."""

    event_id: str
    path: Path
    written_at: datetime
    retention_deadline: datetime

    def __post_init__(self) -> None:
        _validate_identifier(self.event_id, "event_id")
        _require_aware(self.written_at, "written_at")
        _require_aware(self.retention_deadline, "retention_deadline")
        if self.retention_deadline < self.written_at:
            raise ValueError("retention_deadline must not precede written_at")


@dataclass(frozen=True)
class EvidenceClipPlanner:
    """Builds a bounded event clip plan from one in-memory frame ring buffer."""

    policy: EvidencePolicy

    def plan(self, event: EvidenceEvent, frames: Sequence[FrameReference]) -> EvidenceClipPlan:
        if not self.policy.evidence_clip_enabled:
            raise EvidencePolicyError("Evidence clips are disabled by policy.")
        mismatched = [frame for frame in frames if frame.stream_session_id != event.stream_session_id]
        if mismatched:
            raise ValueError("Frame ring buffer contains a different stream session.")

        start_at = event.occurred_at - timedelta(seconds=self.policy.pre_event_seconds)
        end_at = event.occurred_at + timedelta(seconds=self.policy.post_event_seconds)
        selected = tuple(
            frame.frame_sequence
            for frame in frames
            if start_at <= frame.captured_at <= end_at
        )
        return EvidenceClipPlan(
            event=event,
            start_at=start_at,
            end_at=end_at,
            frame_sequences=selected,
            retention_deadline=event.occurred_at + timedelta(days=self.policy.retention_days),
        )


@dataclass(frozen=True)
class LocalEvidenceDirectory:
    """Local evidence directory with injected encrypted persistence.

    The writer is required to perform encryption; this class only creates a
    constrained local target path and never represents plaintext storage as
    encrypted storage.
    """

    directory: Path
    writer: EncryptedEvidenceWriter

    def write_clip(
        self,
        event_id: str,
        payload: bytes,
        written_at: datetime,
        retention_deadline: datetime,
    ) -> EvidenceRecord:
        _validate_identifier(event_id, "event_id")
        _require_aware(written_at, "written_at")
        _require_aware(retention_deadline, "retention_deadline")
        if retention_deadline < written_at:
            raise ValueError("retention_deadline must not precede written_at")
        if not isinstance(payload, bytes):
            raise TypeError("Evidence payload must be bytes.")

        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{event_id}.evidence"
        self._require_contained(target)
        self.writer.write_encrypted(target, payload)
        return EvidenceRecord(event_id, target, written_at, retention_deadline)

    def enforce_retention(
        self, records: Sequence[EvidenceRecord], now: datetime
    ) -> tuple[str, ...]:
        """Delete expired evidence only after validating every candidate path."""
        _require_aware(now, "now")
        expired = tuple(record for record in records if record.retention_deadline <= now)
        for record in expired:
            self._require_contained(record.path)
        for record in expired:
            if record.path.exists():
                record.path.unlink()
        return tuple(record.event_id for record in expired)

    def _require_contained(self, path: Path) -> None:
        root = self.directory.resolve()
        candidate = path.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("Evidence path is outside the configured directory.") from error


def load_evidence_policy(path: Path = DEFAULT_EVIDENCE_POLICY_PATH) -> EvidencePolicy:
    """Load the strict P0 evidence configuration, failing closed on malformed YAML."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvidencePolicyError(f"Cannot read evidence policy: {path}") from error
    except yaml.YAMLError as error:
        raise EvidencePolicyError(f"Invalid evidence policy YAML: {path}") from error
    if not isinstance(document, dict) or document.get("version") != EVIDENCE_POLICY_VERSION:
        raise EvidencePolicyError(f"Evidence policy must declare version {EVIDENCE_POLICY_VERSION}.")

    recording = document.get("recording")
    if not isinstance(recording, dict):
        raise EvidencePolicyError("Evidence policy recording section is required.")
    clip = recording.get("evidence_clip")
    full_session = recording.get("full_session_recording")
    if not isinstance(clip, dict) or not isinstance(full_session, dict):
        raise EvidencePolicyError("Evidence clip and full-session policy sections are required.")

    default_mode = recording.get("default_mode")
    if default_mode != "metadata_only":
        raise EvidencePolicyError("P0 recording default_mode must be metadata_only.")
    enabled = clip.get("enabled")
    full_enabled = full_session.get("enabled")
    values = (clip.get("pre_event_seconds"), clip.get("post_event_seconds"), clip.get("retention_days"))
    if not isinstance(enabled, bool) or not isinstance(full_enabled, bool):
        raise EvidencePolicyError("Evidence recording enablement flags must be boolean.")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values):
        raise EvidencePolicyError("Evidence clip durations and retention must be positive integers.")

    return EvidencePolicy(
        version=EVIDENCE_POLICY_VERSION,
        default_mode=default_mode,
        evidence_clip_enabled=enabled,
        pre_event_seconds=values[0],
        post_event_seconds=values[1],
        retention_days=values[2],
        full_session_recording_enabled=full_enabled,
    )


def _freeze_mapping(metadata: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(metadata, Mapping):
        raise TypeError("Evidence metadata must be a mapping.")
    return MappingProxyType({str(key): _freeze_value(value) for key, value in metadata.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _validate_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or Path(value).name != value or value in {".", ".."}:
        raise ValueError(f"{name} must be a non-empty identifier without path components.")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
