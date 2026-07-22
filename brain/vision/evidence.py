"""Immutable P0 evidence contracts and local retention enforcement.

This module is observation-only.  It intentionally has no video codec,
transport, or flight-control dependency. Deployments may supply any compliant
``EncryptedEvidenceWriter``; the optional Fernet writer below is a local P0
implementation with caller-provisioned key material.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

import yaml

from .contracts import CameraFrame


DEFAULT_EVIDENCE_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "shared/config/vision/evidence.v1.yaml"
)
EVIDENCE_POLICY_VERSION = "v1"


class EvidencePolicyError(ValueError):
    """Raised when the versioned evidence policy is incomplete or unsafe."""


class EvidenceEncryptionError(RuntimeError):
    """Evidence encryption cannot proceed safely with the local runtime/key."""


class EvidenceCaptureError(ValueError):
    """An explicit evidence clip request cannot safely be fulfilled."""


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
class CapturedEvidence:
    """Metadata-only handle to one explicitly requested encrypted clip."""

    record: EvidenceRecord
    frame_sequences: tuple[int, ...]


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


@dataclass(frozen=True)
class _BufferedFrame:
    frame: CameraFrame
    payload: bytes


class EvidenceCaptureBuffer:
    """Bounded in-memory frame ring for explicit, event-only evidence clips.

    Recording a frame only retains it in this process memory. Disk writes occur
    solely after ``request`` and a later ``finalize`` once the policy's
    post-event window has elapsed. The serialized clip is handed to the
    configured encrypted evidence directory; this class never writes plaintext.
    """

    def __init__(
        self,
        policy: EvidencePolicy,
        directory: LocalEvidenceDirectory,
        *,
        max_frames: int,
        max_payload_bytes: int,
    ) -> None:
        if not isinstance(policy, EvidencePolicy):
            raise EvidenceCaptureError("Evidence capture requires an EvidencePolicy.")
        if not isinstance(directory, LocalEvidenceDirectory):
            raise EvidenceCaptureError("Evidence capture requires a LocalEvidenceDirectory.")
        if type(max_frames) is not int or max_frames <= 0:
            raise EvidenceCaptureError("Evidence capture max_frames must be a positive integer.")
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise EvidenceCaptureError("Evidence capture max_payload_bytes must be a positive integer.")
        self._policy = policy
        self._directory = directory
        self._max_frames = max_frames
        self._max_payload_bytes = max_payload_bytes
        self._frames: tuple[_BufferedFrame, ...] = ()
        self._pending: dict[str, EvidenceEvent] = {}
        self._records: tuple[EvidenceRecord, ...] = ()

    def record(self, frame: CameraFrame, payload: bytes) -> None:
        """Retain one validated frame in memory only, evicting oldest evidence."""
        if not isinstance(frame, CameraFrame):
            raise EvidenceCaptureError("Evidence capture requires a CameraFrame.")
        if not isinstance(payload, bytes) or not payload:
            raise EvidenceCaptureError("Evidence capture payload must be non-empty bytes.")
        if len(payload) > self._max_payload_bytes:
            raise EvidenceCaptureError("Evidence capture payload exceeds the configured memory bound.")
        if hashlib.sha256(payload).hexdigest() != frame.payload_hash:
            raise EvidenceCaptureError("Evidence capture payload hash does not match its CameraFrame.")
        replacement = _BufferedFrame(frame, payload)
        retained = tuple(item for item in self._frames if not _same_frame(item.frame, frame)) + (replacement,)
        self._frames = retained[-self._max_frames:]

    def request(self, event: EvidenceEvent) -> None:
        """Queue a caller-approved event; this does not write a file yet."""
        if not isinstance(event, EvidenceEvent):
            raise EvidenceCaptureError("Evidence capture requires an EvidenceEvent.")
        if event.event_id in self._pending or any(record.event_id == event.event_id for record in self._records):
            raise EvidenceCaptureError("Evidence event ID is already recorded or pending.")
        if not any(item.frame.stream_session_id == event.stream_session_id for item in self._frames):
            raise EvidenceCaptureError("Evidence event stream session is not present in the memory buffer.")
        self._pending = {**self._pending, event.event_id: event}

    def finalize(self, event_id: str, *, finalized_at: datetime) -> CapturedEvidence:
        """Encrypt the selected clip only after its full post-event period ends."""
        if not isinstance(event_id, str) or not event_id:
            raise EvidenceCaptureError("Evidence event ID is required.")
        _require_aware(finalized_at, "finalized_at")
        try:
            event = self._pending[event_id]
        except KeyError as error:
            raise EvidenceCaptureError("Evidence event is not pending.") from error
        if finalized_at < event.occurred_at + timedelta(seconds=self._policy.post_event_seconds):
            raise EvidenceCaptureError("Evidence post-event window has not elapsed.")
        session_frames = tuple(
            item for item in self._frames if item.frame.stream_session_id == event.stream_session_id
        )
        if not any(
            item.frame.captured_at >= event.occurred_at + timedelta(seconds=self._policy.post_event_seconds)
            for item in session_frames
        ):
            raise EvidenceCaptureError("Evidence buffer does not contain complete post-event coverage.")
        plan = EvidenceClipPlanner(self._policy).plan(
            event,
            tuple(FrameReference(item.frame.frame_sequence, item.frame.captured_at, item.frame.stream_session_id) for item in session_frames),
        )
        selected = tuple(item for item in session_frames if item.frame.frame_sequence in plan.frame_sequences)
        plaintext = _clip_document(event, selected)
        record = self._directory.write_clip(event.event_id, plaintext, finalized_at, plan.retention_deadline)
        self._pending = {key: value for key, value in self._pending.items() if key != event_id}
        self._records = self._records + (record,)
        return CapturedEvidence(record, plan.frame_sequences)

    def enforce_retention(self, now: datetime) -> tuple[str, ...]:
        """Remove expired encrypted clips tracked by this capture buffer."""
        expired = self._directory.enforce_retention(self._records, now)
        if expired:
            removed = frozenset(expired)
            self._records = tuple(record for record in self._records if record.event_id not in removed)
        return expired


def _same_frame(left: CameraFrame, right: CameraFrame) -> bool:
    return (
        left.device_id, left.camera_id, left.stream_session_id, left.frame_sequence
    ) == (
        right.device_id, right.camera_id, right.stream_session_id, right.frame_sequence
    )


def _clip_document(event: EvidenceEvent, frames: Sequence[_BufferedFrame]) -> bytes:
    document = {
        "contract_version": "vision_evidence_clip.v1",
        "event_id": event.event_id,
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "stream_session_id": event.stream_session_id,
        "metadata": _thaw_mapping(event.metadata),
        "frames": [
            {
                "frame_sequence": item.frame.frame_sequence,
                "captured_at": item.frame.captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "payload_hash": item.frame.payload_hash,
                "encoding": item.frame.encoding,
                "width_px": item.frame.width_px,
                "height_px": item.frame.height_px,
                "payload_base64": base64.b64encode(item.payload).decode("ascii"),
            }
            for item in frames
        ],
    }
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EvidenceCaptureError("Evidence event metadata must be JSON-compatible.") from error


def _thaw_mapping(value: Mapping[str, object]) -> dict[str, object]:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [thaw(child) for child in item]
        return item

    return {str(key): thaw(item) for key, item in value.items()}


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
