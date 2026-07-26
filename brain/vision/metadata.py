"""Append-only local metadata journal for P0 observation artifacts.

The journal records only the existing read-only dashboard document plus a
writer timestamp and local sequence.  It deliberately rejects payloads,
evidence locations, templates, embeddings, and arbitrary future fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


METADATA_CONTRACT_VERSION = "vision_metadata.v1"
_DASHBOARD_FIELDS = frozenset({
    "contract_version", "state", "observed_at", "track_count", "detections",
    "backlog_frames", "dropped_frames", "stream_state", "model_state", "gpu_state",
})


class VisionMetadataError(ValueError):
    """The local journal or attempted observation metadata is unsafe."""


@dataclass(frozen=True)
class VisionMetadataRecord:
    """Immutable, metadata-only local audit record."""

    contract_version: str
    sequence: int
    written_at: datetime
    status: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.contract_version != METADATA_CONTRACT_VERSION:
            raise VisionMetadataError("Unsupported Vision metadata contract version.")
        if type(self.sequence) is not int or self.sequence < 0:
            raise VisionMetadataError("Vision metadata sequence must be a non-negative integer.")
        if self.written_at.tzinfo is None or self.written_at.utcoffset() is None:
            raise VisionMetadataError("Vision metadata written_at must be timezone-aware.")
        object.__setattr__(self, "status", MappingProxyType(_validated_status(self.status)))

    def document(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "sequence": self.sequence,
            "written_at": self.written_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "status": _thaw(self.status),
        }


class LocalVisionMetadataStore:
    """Strict, append-only JSONL metadata store for one local Vision runtime.

    This intentionally owns a single process-local writer.  The record is
    encoded as one bounded write to an append-only file; a second runtime must
    use a different path or an external P3 store.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.name:
            raise VisionMetadataError("Metadata path must be a concrete file path.")
        self._path = path
        self._next_sequence = self._read_next_sequence()

    @property
    def path(self) -> Path:
        return self._path

    def append_dashboard_status(
        self, status: Mapping[str, object], *, written_at: datetime
    ) -> VisionMetadataRecord:
        """Persist one validated dashboard read model without any raw evidence."""
        record = VisionMetadataRecord(
            METADATA_CONTRACT_VERSION, self._next_sequence, written_at, status,
        )
        encoded = json.dumps(record.document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._path.open("a", encoding="utf-8") as journal:
                journal.write(encoded + "\n")
                journal.flush()
        except OSError as error:
            raise VisionMetadataError(f"Cannot append Vision metadata: {error}") from error
        self._next_sequence += 1
        return record

    def _read_next_sequence(self) -> int:
        if not self._path.exists():
            return 0
        if not self._path.is_file():
            raise VisionMetadataError("Metadata path must refer to a regular file.")
        expected = 0
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise VisionMetadataError(f"Cannot read Vision metadata: {error}") from error
        for line_number, line in enumerate(lines, start=1):
            try:
                document = json.loads(line)
                record = _record_from_document(document)
            except (json.JSONDecodeError, TypeError, KeyError, VisionMetadataError, ValueError) as error:
                raise VisionMetadataError(f"Invalid Vision metadata journal line {line_number}: {error}") from error
            if record.sequence != expected:
                raise VisionMetadataError(f"Invalid Vision metadata journal line {line_number}: sequence must equal {expected}.")
            expected += 1
        return expected


def _record_from_document(document: object) -> VisionMetadataRecord:
    if not isinstance(document, dict):
        raise VisionMetadataError("metadata record must be a JSON object")
    if set(document) != {"contract_version", "sequence", "written_at", "status"}:
        raise VisionMetadataError("metadata record has unsupported fields")
    written_at = document["written_at"]
    if not isinstance(written_at, str):
        raise VisionMetadataError("metadata written_at must be an RFC3339 string")
    return VisionMetadataRecord(
        document["contract_version"], document["sequence"],
        datetime.fromisoformat(written_at.replace("Z", "+00:00")), document["status"],
    )


def _validated_status(status: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(status, Mapping) or set(status) != _DASHBOARD_FIELDS:
        raise VisionMetadataError("Metadata status must use exactly the read-only dashboard fields.")
    if status.get("contract_version") != "vision_dashboard.v1":
        raise VisionMetadataError("Metadata status must declare vision_dashboard.v1.")
    if status.get("state") not in {"valid", "missing", "stale", "invalid"}:
        raise VisionMetadataError("Metadata status has an invalid state.")
    if not isinstance(status.get("observed_at"), str):
        raise VisionMetadataError("Metadata status observed_at must be a timestamp string.")
    for field in ("track_count", "backlog_frames", "dropped_frames"):
        value = status.get(field)
        if type(value) is not int or value < 0:
            raise VisionMetadataError(f"Metadata status {field} must be a non-negative integer.")
    if any(status.get(field) not in {"healthy", "degraded", "unavailable", "missing"} for field in ("stream_state", "model_state", "gpu_state")):
        raise VisionMetadataError("Metadata status has an invalid health state.")
    detections = status.get("detections")
    if not isinstance(detections, list) or not all(_valid_detection(item) for item in detections):
        raise VisionMetadataError("Metadata status detections must use the read-only dashboard schema.")
    return {str(key): _freeze(value) for key, value in status.items()}


def _valid_detection(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"label", "confidence", "tracker_id", "bounding_box"}:
        return False
    confidence = value.get("confidence")
    box = value.get("bounding_box")
    return (
        isinstance(value.get("label"), str)
        and type(confidence) in (int, float) and isfinite(confidence) and 0.0 <= confidence <= 1.0
        and (value.get("tracker_id") is None or isinstance(value.get("tracker_id"), str))
        and isinstance(box, Mapping) and set(box) == {"x_px", "y_px", "width_px", "height_px"}
        and type(box["x_px"]) is int and box["x_px"] >= 0
        and type(box["y_px"]) is int and box["y_px"] >= 0
        and type(box["width_px"]) is int and box["width_px"] > 0
        and type(box["height_px"]) is int and box["height_px"] > 0
    )


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
