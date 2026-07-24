"""The hardware-independent frame contract every perception sensor speaks.

ByteWolf is a general embodied system, not a single-camera demo, so a frame is
never assumed to be one camera in one format. A ``CameraFrame`` names the sensor
it came from and the encoding of its bytes -- raw RGB, YUV, grayscale, depth, or
a compressed stream -- so the perception stack stays multi-sensor and format-
agnostic. JPEG is one encoding among many here, a streaming and UI convenience,
not the basis of the architecture.

The contract is deliberately thin: it carries pixels and the metadata needed to
interpret them, and nothing about how they were captured or transported. That is
what keeps it independent of the compute platform -- the same frame shape comes
off a Gazebo camera today and a real camera on a Raspberry Pi, Jetson, or AI
accelerator later, through the same adapter boundary.

A raw frame whose byte count does not match its dimensions and encoding is
refused rather than passed on, because a malformed frame reaching a detector is
how a perception pipeline silently starts seeing things that are not there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class CameraFrameError(ValueError):
    """Raised when a frame cannot be trusted as the pixels it claims to be."""


class FrameEncoding(Enum):
    """How to read a frame's bytes. Grouped by kind, not by any one vendor."""

    RGB8 = "rgb8"
    BGR8 = "bgr8"
    MONO8 = "mono8"
    YUV422 = "yuv422"
    DEPTH16 = "depth16"
    DEPTH32F = "depth32f"
    JPEG = "jpeg"

    @property
    def bytes_per_pixel(self) -> int | None:
        """Fixed stride for a raw encoding, or ``None`` when the size is opaque."""
        return _BYTES_PER_PIXEL.get(self)

    @property
    def is_raw(self) -> bool:
        return self in _RAW_COLOR

    @property
    def is_depth(self) -> bool:
        return self in (FrameEncoding.DEPTH16, FrameEncoding.DEPTH32F)

    @property
    def is_compressed(self) -> bool:
        return self is FrameEncoding.JPEG


_BYTES_PER_PIXEL: dict[FrameEncoding, int] = {
    FrameEncoding.RGB8: 3,
    FrameEncoding.BGR8: 3,
    FrameEncoding.MONO8: 1,
    FrameEncoding.YUV422: 2,
    FrameEncoding.DEPTH16: 2,
    FrameEncoding.DEPTH32F: 4,
}
_RAW_COLOR = frozenset(
    {FrameEncoding.RGB8, FrameEncoding.BGR8, FrameEncoding.MONO8, FrameEncoding.YUV422}
)


@dataclass(frozen=True)
class CameraFrame:
    """One captured frame from a named sensor, in a declared encoding.

    ``sensor_id`` is the free-form identity of the source -- ``front_rgb``,
    ``down_rgb``, ``depth`` -- so a multi-sensor rig routes frames without the
    contract hard-coding any sensor set.
    """

    sensor_id: str
    encoding: FrameEncoding
    width: int
    height: int
    data: bytes
    captured_at: datetime
    frame_id: str | None = None

    def __post_init__(self) -> None:
        if not self.sensor_id:
            raise CameraFrameError("A frame must name the sensor it came from.")
        if not isinstance(self.encoding, FrameEncoding):
            raise CameraFrameError("A frame must declare a known encoding.")
        if self.width <= 0 or self.height <= 0:
            raise CameraFrameError("A frame must have positive dimensions.")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise CameraFrameError("A frame's capture time must be timezone-aware.")

    @property
    def expected_raw_byte_count(self) -> int | None:
        """The exact byte count a raw frame must carry, or ``None`` if compressed."""
        stride = self.encoding.bytes_per_pixel
        return None if stride is None else self.width * self.height * stride

    def is_well_formed(self) -> bool:
        """Whether the bytes match the dimensions and encoding claimed.

        A compressed frame is only checked for presence; its length is opaque. A
        raw frame's length must be exactly width * height * bytes-per-pixel.
        """
        if not self.data:
            return False
        expected = self.expected_raw_byte_count
        return expected is None or len(self.data) == expected

    def utc_captured_at(self) -> datetime:
        return self.captured_at.astimezone(UTC)
