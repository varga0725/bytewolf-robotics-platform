"""Ingest frames from a local UVC (USB) camera, such as the Hawkeye in PC-CAM mode.

The camera is opened through an injected capture factory, so the pipeline can be
exercised without hardware: the factory yields anything with ``isOpened``,
``read``, ``get`` and ``release`` (OpenCV's ``VideoCapture`` satisfies it). Each
captured frame becomes a ``CameraFrame`` bound to its payload by sha256, and the
source doubles as the detector's ``PayloadResolver`` for exactly that payload --
so a detector can never analyse bytes the frame does not attest to.

Observation-only: nothing here reaches a flight, actuator or PX4 interface.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from typing import Any, Protocol

from .contracts import CameraFrame, VisionContractError

CAMERA_FRAME_V1 = "camera_frame.v1"
DEFAULT_JPEG_QUALITY = 90


class Capture(Protocol):
    """The slice of OpenCV's VideoCapture this source depends on."""

    def isOpened(self) -> bool: ...  # noqa: N802 - OpenCV's name
    def read(self) -> tuple[bool, Any]: ...
    def get(self, prop: int) -> float: ...
    def release(self) -> None: ...


class UvcCaptureError(RuntimeError):
    """The camera could not be opened or produced no usable frame."""


def _default_capture_factory(index: int) -> Capture:
    import cv2  # imported lazily: the contract layer must not need OpenCV

    return cv2.VideoCapture(index)


def _default_encoder(image: Any, quality: int) -> bytes:
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise UvcCaptureError("Frame could not be JPEG-encoded.")
    return bytes(buffer)


class UvcCameraSource:
    """One local UVC camera, yielding hash-bound CameraFrames and their payloads."""

    def __init__(
        self,
        device_index: int,
        *,
        device_id: str,
        camera_id: str,
        calibration_version: str = "uncalibrated",
        capture_factory: Callable[[int], Capture] = _default_capture_factory,
        encoder: Callable[[Any, int], bytes] = _default_encoder,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        stream_session_id: str | None = None,
    ) -> None:
        if not device_id.strip() or not camera_id.strip():
            raise VisionContractError("A UVC source requires a device id and a camera id.")
        self._index = device_index
        self._device_id = device_id
        self._camera_id = camera_id
        self._calibration_version = calibration_version
        self._capture_factory = capture_factory
        self._encoder = encoder
        self._jpeg_quality = jpeg_quality
        self._now = now
        # The session id binds a run of frames together; a reconnect starts a new
        # session so a consumer can tell a sequence gap from a fresh stream.
        self._stream_session_id = stream_session_id or f"uvc-{device_index}-{int(self._now().timestamp())}"
        self._capture: Capture | None = None
        self._sequence = 0
        self._dropped = 0
        self._last_payload: bytes | None = None
        self._last_hash: str | None = None

    # -- lifecycle --------------------------------------------------------

    def open(self) -> None:
        capture = self._capture_factory(self._index)
        if not capture.isOpened():
            capture.release()
            raise UvcCaptureError(
                f"UVC device index {self._index} could not be opened. On macOS this is either the wrong "
                "index or a camera permission the capturing process does not hold."
            )
        self._capture = capture

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> UvcCameraSource:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- capture ----------------------------------------------------------

    def capture_once(self) -> tuple[CameraFrame, bytes]:
        """Capture one frame, or raise if the device gave nothing usable."""
        if self._capture is None:
            raise UvcCaptureError("The UVC source is not open.")
        captured_at = self._now()
        ok, image = self._capture.read()
        received_at = self._now()
        if not ok or image is None:
            self._dropped += 1
            raise UvcCaptureError("The UVC device returned no frame.")
        payload = self._encoder(image, self._jpeg_quality)
        payload_hash = hashlib.sha256(payload).hexdigest()
        height_px, width_px = int(image.shape[0]), int(image.shape[1])
        self._sequence += 1
        frame = CameraFrame(
            CAMERA_FRAME_V1,
            self._device_id,
            self._camera_id,
            self._stream_session_id,
            self._sequence,
            captured_at,
            received_at,
            self._calibration_version,
            payload_hash,
            "jpeg",
            width_px,
            height_px,
            max(0.0, (received_at - captured_at).total_seconds() * 1000.0),
            self._dropped,
        )
        self._last_payload = payload
        self._last_hash = payload_hash
        return frame, payload

    # -- PayloadResolver --------------------------------------------------

    def resolve(self, payload_hash: str) -> bytes:
        """Return the bytes for the frame just captured, and only those.

        A detector asks by hash; anything other than the current payload is
        refused, so stale or foreign bytes can never be analysed as this frame.
        """
        if self._last_payload is None or payload_hash != self._last_hash:
            raise UvcCaptureError("No captured payload matches that hash.")
        return self._last_payload

    @property
    def stream_session_id(self) -> str:
        return self._stream_session_id

    @property
    def dropped_frames(self) -> int:
        return self._dropped
