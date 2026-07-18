"""Durable PX4 ULog capture metadata for offline, read-only flight analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile


ULOG_MANIFEST_VERSION = "v0.1"


class ULogCaptureError(ValueError):
    """Raised when a raw PX4 log cannot be archived with verifiable provenance."""


@dataclass(frozen=True)
class ULogCapture:
    """Immutable manifest entry for one archived raw PX4 log."""

    run_id: str
    relative_path: str
    sha256_hex: str
    size_bytes: int
    captured_at: datetime

    def to_document(self) -> dict[str, object]:
        return {
            "captured_at": self.captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "relative_path": self.relative_path,
            "run_id": self.run_id,
            "sha256": self.sha256_hex,
            "size_bytes": self.size_bytes,
            "version": ULOG_MANIFEST_VERSION,
        }


def write_ulog_unavailable_manifest(artifact_directory: Path, run_id: str, reason: str) -> Path:
    """Record that this run had no archiveable PX4 raw log, without claiming capture."""
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ULogCaptureError("ULog run_id must be a non-empty filename component.")
    if not isinstance(reason, str) or not reason:
        raise ULogCaptureError("ULog unavailable reason must be non-empty.")
    directory = artifact_directory / "px4-ulogs"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{run_id}.manifest.json"
    _write_document(
        destination,
        {
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "reason": reason,
            "run_id": run_id,
            "status": "unavailable",
            "version": ULOG_MANIFEST_VERSION,
        },
    )
    return destination


def archive_px4_ulog(source: Path, artifact_directory: Path, run_id: str) -> ULogCapture:
    """Copy a completed `.ulg` file into the run bundle and write its manifest atomically."""
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ULogCaptureError("ULog run_id must be a non-empty filename component.")
    if source.suffix.lower() != ".ulg":
        raise ULogCaptureError("PX4 raw log must have a .ulg extension.")
    if not source.is_file():
        raise ULogCaptureError(f"PX4 raw log '{source}' is not a readable file.")

    archive_directory = artifact_directory / "px4-ulogs"
    destination = archive_directory / f"{run_id}.ulg"
    if destination.exists():
        raise ULogCaptureError(f"PX4 raw log destination '{destination}' already exists.")
    archive_directory.mkdir(parents=True, exist_ok=True)
    temporary = _copy_to_temporary(source, archive_directory)
    try:
        digest, size = _hash_file(temporary)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    capture = ULogCapture(
        run_id=run_id,
        relative_path=str(destination.relative_to(artifact_directory)),
        sha256_hex=digest,
        size_bytes=size,
        captured_at=datetime.now(UTC),
    )
    _write_manifest(archive_directory / f"{run_id}.manifest.json", capture)
    return capture


def _copy_to_temporary(source: Path, directory: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", suffix=".ulg", dir=directory)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _hash_file(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_manifest(destination: Path, capture: ULogCapture) -> None:
    _write_document(destination, {**capture.to_document(), "status": "captured"})


def _write_document(destination: Path, document: dict[str, object]) -> None:
    payload = json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, prefix=".pending-", delete=False
    ) as output:
        temporary = Path(output.name)
        output.write(payload)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(destination)
