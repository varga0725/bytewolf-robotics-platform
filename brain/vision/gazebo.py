"""Optional Gazebo RGB image ingest adapter.

The adapter intentionally imports no Gazebo package.  A caller supplies the
small subscription binding from its Gazebo/ROS bridge, keeping the Vision Core
importable in CI, recorded-fixture tests, and deployments without a simulator.
It turns only strictly valid RGB evidence into :class:`CameraFrame` contracts;
malformed simulator messages are recorded as degraded stream health, never
represented as an empty detection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from .contracts import CAMERA_FRAME_V1, CameraFrame, VisionHealth


class GazeboImageValidationError(ValueError):
    """Raised when a binding or simulator image violates the ingest boundary."""


class GazeboSubscriptionBindings(Protocol):
    """The only Gazebo-facing dependency required by this adapter.

    ``subscribe`` must register ``callback`` for a topic and return an
    unsubscribe callable.  The concrete binding may wrap gz-transport,
    ros_gz_bridge, or a test fixture, but is never allowed to provide actuator
    access to this observation-only module.
    """

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> Callable[[], None]: ...


@dataclass(frozen=True)
class GazeboIngestLifecycle:
    """Read-only stream lifecycle evidence for dashboard and runtime health."""

    observed_at: datetime
    stream_state: str
    stream_session_id: str
    reconnect_count: int
    dropped_frames: int
    rejected_messages: int
    reason: str = ""


class GazeboImageIngestAdapter:
    """Single-slot, newest-frame-wins adapter for a Gazebo RGB image topic."""

    def __init__(
        self,
        *,
        bindings: GazeboSubscriptionBindings,
        topic: str,
        device_id: str,
        camera_id: str,
        calibration_version: str,
        clock: Callable[[], datetime] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        max_clock_skew: timedelta = timedelta(seconds=2),
    ) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (topic, device_id, camera_id, calibration_version)):
            raise GazeboImageValidationError("Topic, device ID, camera ID, and calibration version are required.")
        if not isinstance(max_clock_skew, timedelta) or max_clock_skew <= timedelta():
            raise GazeboImageValidationError("max_clock_skew must be a positive duration.")
        if not callable(getattr(bindings, "subscribe", None)):
            raise GazeboImageValidationError("Gazebo bindings must provide a callable subscribe method.")
        self._bindings = bindings
        self._topic = topic
        self._device_id = device_id
        self._camera_id = camera_id
        self._calibration_version = calibration_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_id_factory = session_id_factory or (lambda: uuid4().hex)
        self._max_clock_skew = max_clock_skew
        self._unsubscribe: Callable[[], None] | None = None
        self._session_id = ""
        self._next_sequence = 0
        self._pending: CameraFrame | None = None
        self._stream_state = "unavailable"
        self._reason = "not started"
        self._reconnect_count = 0
        self._dropped_frames = 0
        self._rejected_messages = 0

    def start(self) -> None:
        """Subscribe once, failing closed if the injected binding is malformed."""
        if self._unsubscribe is not None:
            return
        self._begin_session()
        try:
            unsubscribe = self._bindings.subscribe(self._topic, self._on_image)
        except Exception as error:
            self._stream_state = "unavailable"
            self._reason = f"subscription failed: {error}"
            raise GazeboImageValidationError(self._reason) from error
        if not callable(unsubscribe):
            self._stream_state = "unavailable"
            self._reason = "subscription binding must return an unsubscribe callable"
            raise GazeboImageValidationError(self._reason)
        self._unsubscribe = unsubscribe
        self._stream_state = "healthy"
        self._reason = ""

    def stop(self, reason: str = "stopped") -> None:
        """Unsubscribe without emitting a command or retaining a raw payload."""
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception as error:
                reason = f"unsubscribe failed: {error}"
        self._pending = None
        self._stream_state = "unavailable"
        self._reason = reason

    def disconnect(self, reason: str, observed_at: datetime) -> None:
        """Expose an external transport loss and end the current session."""
        _require_aware(observed_at, "observed_at")
        self.stop(reason or "transport disconnected")

    def reconnect(self, observed_at: datetime) -> None:
        """Open a fresh subscription/session after a transport interruption."""
        _require_aware(observed_at, "observed_at")
        self.stop("reconnecting")
        self._reconnect_count += 1
        self.start()

    def poll(self) -> CameraFrame | None:
        """Return the newest accepted frame once; payload bytes never leave this boundary."""
        frame, self._pending = self._pending, None
        return frame

    def lifecycle(self, observed_at: datetime) -> GazeboIngestLifecycle:
        _require_aware(observed_at, "observed_at")
        return GazeboIngestLifecycle(
            observed_at=observed_at,
            stream_state=self._stream_state,
            stream_session_id=self._session_id,
            reconnect_count=self._reconnect_count,
            dropped_frames=self._dropped_frames,
            rejected_messages=self._rejected_messages,
            reason=self._reason,
        )

    def health(self, observed_at: datetime) -> VisionHealth:
        """Render adapter lifecycle as the standard Vision health contract."""
        _require_aware(observed_at, "observed_at")
        return VisionHealth(
            observed_at=observed_at,
            stream_state=self._stream_state,
            model_state="missing",
            gpu_state="missing",
            backlog_frames=1 if self._pending is not None else 0,
            dropped_frames=self._dropped_frames,
        )

    def _begin_session(self) -> None:
        session_id = self._session_id_factory()
        if not isinstance(session_id, str) or not session_id.strip():
            raise GazeboImageValidationError("session_id_factory must return a non-empty string.")
        self._session_id = session_id
        self._next_sequence = 0
        self._pending = None

    def _on_image(self, message: object) -> None:
        """Convert one Gazebo image callback without letting callback failures escape."""
        if self._stream_state == "unavailable":
            return
        try:
            frame = self._build_frame(message, self._clock())
        except GazeboImageValidationError as error:
            self._rejected_messages += 1
            self._stream_state = "degraded"
            self._reason = str(error)
            return
        if self._pending is not None:
            self._dropped_frames += 1
        # Frame metadata reports the complete loss count at the instant this
        # newest evidence became current, so downstream records can be read
        # without dereferencing mutable adapter state.
        if self._dropped_frames != frame.dropped_frames:
            frame = CameraFrame(**{**frame.__dict__, "dropped_frames": self._dropped_frames})
        self._pending = frame
        self._next_sequence += 1
        self._stream_state = "healthy"
        self._reason = ""

    def _build_frame(self, message: object, received_at: datetime) -> CameraFrame:
        _require_aware(received_at, "received_at")
        width = _positive_int(_field(message, "width"), "width")
        height = _positive_int(_field(message, "height"), "height")
        step = _positive_int(_field(message, "step"), "step")
        pixel_format = _field(message, "pixel_format")
        if pixel_format not in ("RGB_INT8", "rgb8"):
            raise GazeboImageValidationError("pixel_format must be RGB_INT8 or rgb8.")
        expected_step = width * 3
        if step != expected_step:
            raise GazeboImageValidationError("RGB step must equal width * 3.")
        payload = _bytes_payload(_field(message, "data"))
        expected_length = step * height
        if len(payload) != expected_length:
            raise GazeboImageValidationError("RGB payload length must exactly match step * height.")
        captured_at = _captured_at(message)
        if captured_at - received_at > self._max_clock_skew:
            raise GazeboImageValidationError("capture timestamp exceeds maximum clock skew.")
        latency_ms = max((received_at - captured_at).total_seconds() * 1000.0, 0.0)
        return CameraFrame(
            contract_version=CAMERA_FRAME_V1,
            device_id=self._device_id,
            camera_id=self._camera_id,
            stream_session_id=self._session_id,
            frame_sequence=self._next_sequence,
            captured_at=captured_at,
            received_at=received_at,
            calibration_version=self._calibration_version,
            payload_hash=sha256(payload).hexdigest(),
            encoding="rgb8",
            width_px=width,
            height_px=height,
            latency_ms=latency_ms,
            dropped_frames=self._dropped_frames,
        )


def _field(value: object, name: str) -> object:
    try:
        return getattr(value, name)
    except AttributeError as error:
        raise GazeboImageValidationError(f"image message is missing {name}.") from error


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise GazeboImageValidationError(f"{name} must be a positive integer.")
    return value


def _bytes_payload(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise GazeboImageValidationError("image data must be bytes-like.")
    return bytes(value)


def _captured_at(message: object) -> datetime:
    header = _field(message, "header")
    stamp = _field(header, "stamp")
    if isinstance(stamp, datetime):
        _require_aware(stamp, "capture timestamp")
        return stamp.astimezone(UTC)
    seconds = getattr(stamp, "sec", getattr(stamp, "seconds", None))
    nanoseconds = getattr(stamp, "nsec", getattr(stamp, "nanos", None))
    if type(seconds) is not int or type(nanoseconds) is not int or not 0 <= nanoseconds < 1_000_000_000:
        raise GazeboImageValidationError("capture timestamp must expose integer sec and nsec fields.")
    try:
        return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=nanoseconds / 1000)
    except (OverflowError, OSError, ValueError) as error:
        raise GazeboImageValidationError("capture timestamp is outside the supported range.") from error


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GazeboImageValidationError(f"{name} must be a timezone-aware datetime.")
