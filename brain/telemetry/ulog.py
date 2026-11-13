"""Durable PX4 ULog capture metadata for offline, read-only flight analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile


ULOG_MANIFEST_VERSION = "v0.1"


class ULogCaptureError(ValueError):
    """Raised when a raw PX4 log cannot be archived with verifiable provenance."""


ULOG_RUN_START_SLACK_S = 5.0


def describe_px4_run_directory() -> tuple[Path | None, str | None]:
    """Resolve the PX4 run directory and explain when it is configured but missing."""
    raw = os.environ.get("BYTEWOLF_PX4_RUN_DIR") or os.environ.get("PX4_RUN_DIR")
    if not raw:
        return None, None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        try:
            path = path.resolve()
        except OSError:
            return None, "configured but missing"
    if path.is_dir():
        return path, None
    if path.exists():
        return None, "configured path is not a directory"
    return None, "configured but missing"


def resolve_px4_run_directory() -> Path | None:
    """Return an absolute PX4 working directory from the environment, if present."""
    path, _reason = describe_px4_run_directory()
    return path


def discover_newest_px4_ulog(
    px4_run_directory: Path,
    *,
    min_mtime_epoch: float | None = None,
    slack_s: float = ULOG_RUN_START_SLACK_S,
) -> Path | None:
    """Return the newest ``.ulg`` under a PX4 SITL working directory, if any."""
    if not px4_run_directory.is_dir():
        return None
    candidates = [path for path in px4_run_directory.rglob("*.ulg") if path.is_file()]
    if min_mtime_epoch is not None:
        floor = min_mtime_epoch - slack_s
        candidates = [path for path in candidates if path.stat().st_mtime >= floor]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


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
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
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
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reason": reason,
            "run_id": run_id,
            "status": "unavailable",
            "version": ULOG_MANIFEST_VERSION,
        },
    )
    return destination


def read_ulog_manifest(artifact_directory: Path, run_id: str) -> dict[str, object] | None:
    manifest = artifact_directory / "px4-ulogs" / f"{run_id}.manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ensure_px4_ulog_archived(
    artifact_directory: Path,
    run_id: str,
    *,
    px4_ulog: Path | None = None,
    run_started_epoch: float | None = None,
    timeout_s: float = 15.0,
    poll_interval_s: float = 0.25,
) -> tuple[str | None, str, str | None]:
    """Poll for a settled ``.ulg`` and archive it, or return status and reason."""
    existing = read_ulog_manifest(artifact_directory, run_id)
    if existing is not None and existing.get("status") == "captured":
        return str(existing.get("relative_path")), "captured", None

    import time

    deadline = time.monotonic() + timeout_s
    last_source: Path | None = None
    last_size: int | None = None
    stable_polls = 0
    while time.monotonic() < deadline:
        if px4_ulog is not None and px4_ulog.is_file():
            source = px4_ulog
        else:
            run_dir = resolve_px4_run_directory()
            source = (
                None
                if run_dir is None
                else discover_newest_px4_ulog(run_dir, min_mtime_epoch=run_started_epoch)
            )
        if source is not None and source.is_file():
            if source is not None:
                size = source.stat().st_size
                if source == last_source and size == last_size and size > 0:
                    stable_polls += 1
                else:
                    stable_polls = 0
                    last_source = source
                    last_size = size
                if stable_polls >= 2:
                    try:
                        capture = archive_px4_ulog(source, artifact_directory, run_id)
                        return capture.relative_path, "captured", None
                    except ULogCaptureError:
                        pass
        existing = read_ulog_manifest(artifact_directory, run_id)
        if existing is not None and existing.get("status") == "captured":
            return str(existing.get("relative_path")), "captured", None
        time.sleep(poll_interval_s)

    run_dir, run_dir_reason = describe_px4_run_directory()
    if px4_ulog is None and run_dir is None:
        if run_dir_reason == "configured but missing":
            reason = "PX4 run directory was configured but does not exist."
        elif run_dir_reason:
            reason = f"PX4 run directory is unusable: {run_dir_reason}"
        else:
            reason = "PX4 run directory was not configured before ULog discovery."
    else:
        reason = "PX4 ULog did not become available before the discovery timeout."
    write_ulog_unavailable_manifest(artifact_directory, run_id, reason)
    return None, "missing", reason


async def wait_for_px4_ulog_archive(
    artifact_directory: Path,
    run_id: str,
    *,
    px4_ulog: Path | None = None,
    run_started_epoch: float | None = None,
    timeout_s: float | None = None,
) -> tuple[str | None, str, str | None]:
    import asyncio
    import functools

    effective_timeout = timeout_s
    if effective_timeout is None:
        raw = os.environ.get("BYTEWOLF_ULOG_DISCOVERY_TIMEOUT_S", "15")
        try:
            effective_timeout = float(raw)
        except ValueError:
            effective_timeout = 15.0
    loop = asyncio.get_running_loop()
    worker = functools.partial(
        ensure_px4_ulog_archived,
        artifact_directory,
        run_id,
        px4_ulog=px4_ulog,
        run_started_epoch=run_started_epoch,
        timeout_s=effective_timeout,
    )
    return await loop.run_in_executor(None, worker)


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
        captured_at=datetime.now(timezone.utc),
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
