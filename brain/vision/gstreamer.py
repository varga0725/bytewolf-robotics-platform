"""Import-safe GStreamer appsink boundary for the observation-only Vision Core.

The module intentionally does not import ``gi``, ``Gst``, camera SDKs, or any
flight-control code.  A small application adapter can implement the protocols
below around a real appsink; unit tests use them directly with in-memory fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
import hashlib
from typing import Callable, Protocol

from .contracts import CAMERA_FRAME_V1, CameraFrame


_MIME_ENCODINGS = {
    "image/jpeg": "jpeg",
    "video/x-h264": "h264",
    "video/x-h265": "h265",
    "video/x-raw-rgb8": "rgb8",
}


class GStreamerIngestError(RuntimeError):
    """Raised when appsink evidence cannot safely become a CameraFrame."""


class GStreamerStreamState(str, Enum):
    """Explicit adapter state; callers must reconnect after a source failure."""

    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StreamBinding:
    """The authenticated device/camera/session association for one appsink."""

    device_id: str
    camera_id: str
    stream_session_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.device_id, self.camera_id, self.stream_session_id)):
            raise ValueError("Stream binding requires non-empty device, camera, and session IDs.")


class AppSinkBuffer(Protocol):
    """Minimal decoded/mapped appsink buffer supplied by a host integration."""

    captured_at: datetime
    mime_type: str
    width_px: int
    height_px: int

    def payload_bytes(self) -> bytes: ...


class AppSinkPipeline(Protocol):
    """A host-owned appsink pipeline; its binding is checked on every pull."""

    binding: StreamBinding

    def pull_buffer(self) -> AppSinkBuffer | None: ...


class GStreamerIngestAdapter:
    """Convert injected appsink buffers to contract-bound, replay-safe frames.

    The adapter owns sequence allocation. A successful reconnect must rotate the
    stream session ID, allowing the domain sequence ledger to treat the source
    as a distinct authenticated stream rather than accepting old frames.
    """

    def __init__(
        self,
        pipeline: AppSinkPipeline,
        *,
        binding: StreamBinding,
        calibration_version: str,
        clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(calibration_version, str) or not calibration_version.strip():
            raise ValueError("calibration_version must be a non-empty string.")
        self._pipeline = pipeline
        self._binding = binding
        self._calibration_version = calibration_version
        self._clock = clock
        self._next_sequence = 0
        self._dropped_frames = 0
        self._stream_state = GStreamerStreamState.HEALTHY
        self._last_error = ""

    @property
    def binding(self) -> StreamBinding:
        return self._binding

    @property
    def stream_state(self) -> GStreamerStreamState:
        return self._stream_state

    @property
    def last_error(self) -> str:
        return self._last_error

    def record_dropped_frames(self, count: int = 1) -> None:
        """Record an externally observed appsink drop without accepting a frame."""
        if type(count) is not int or count < 0:
            raise ValueError("Dropped-frame count must be a non-negative integer.")
        self._dropped_frames += count

    def disconnect(self, reason: str) -> None:
        """Make the source fail closed until a new authenticated session reconnects."""
        self._stream_state = GStreamerStreamState.UNAVAILABLE
        self._last_error = reason or "stream disconnected"

    def reconnect(self, stream_session_id: str) -> None:
        """Accept a new session and reset sequence allocation for that session."""
        if not isinstance(stream_session_id, str) or not stream_session_id.strip():
            raise ValueError("A reconnect requires a non-empty stream session ID.")
        if stream_session_id == self._binding.stream_session_id:
            raise ValueError("A reconnect must rotate the stream session ID.")
        self._binding = replace(self._binding, stream_session_id=stream_session_id)
        self._next_sequence = 0
        self._stream_state = GStreamerStreamState.HEALTHY
        self._last_error = ""

    def poll(self) -> CameraFrame | None:
        """Pull one buffer, or raise a fail-closed error for malformed evidence."""
        if self._stream_state is not GStreamerStreamState.HEALTHY:
            raise GStreamerIngestError("GStreamer stream is unavailable; reconnect is required.")
        try:
            if self._pipeline.binding != self._binding:
                raise GStreamerIngestError("Pipeline binding does not match the authenticated stream binding.")
            buffer = self._pipeline.pull_buffer()
            if buffer is None:
                return None
            frame = self._to_frame(buffer)
        except GStreamerIngestError as error:
            self.disconnect(str(error))
            raise
        except Exception as error:
            message = f"GStreamer appsink pull failed: {error}"
            self.disconnect(message)
            raise GStreamerIngestError(message) from error
        self._next_sequence += 1
        return frame

    def _to_frame(self, buffer: AppSinkBuffer) -> CameraFrame:
        encoding = _MIME_ENCODINGS.get(buffer.mime_type)
        if encoding is None:
            raise GStreamerIngestError(f"Unsupported GStreamer MIME type: {buffer.mime_type!r}.")
        payload = buffer.payload_bytes()
        if not isinstance(payload, bytes) or not payload:
            raise GStreamerIngestError("Appsink payload must be non-empty bytes.")
        if type(buffer.width_px) is not int or type(buffer.height_px) is not int or buffer.width_px <= 0 or buffer.height_px <= 0:
            raise GStreamerIngestError("Appsink dimensions must be positive integers.")
        captured_at = buffer.captured_at
        received_at = self._clock()
        if not _aware(captured_at) or not _aware(received_at):
            raise GStreamerIngestError("Appsink capture and receive timestamps must be timezone-aware.")
        captured_utc = captured_at.astimezone(UTC)
        received_utc = received_at.astimezone(UTC)
        if captured_utc > received_utc:
            raise GStreamerIngestError("Appsink capture timestamp is after its receive timestamp.")
        return CameraFrame(
            contract_version=CAMERA_FRAME_V1,
            device_id=self._binding.device_id,
            camera_id=self._binding.camera_id,
            stream_session_id=self._binding.stream_session_id,
            frame_sequence=self._next_sequence,
            captured_at=captured_utc,
            received_at=received_utc,
            calibration_version=self._calibration_version,
            payload_hash=hashlib.sha256(payload).hexdigest(),
            encoding=encoding,
            width_px=buffer.width_px,
            height_px=buffer.height_px,
            latency_ms=(received_utc - captured_utc).total_seconds() * 1000,
            dropped_frames=self._dropped_frames,
        )


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
