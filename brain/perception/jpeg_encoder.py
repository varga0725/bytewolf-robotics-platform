"""Encode a raw RGB frame to JPEG for the live dashboard stream.

At 1080p a raw frame is 6 MB and a lossless PNG is still megabytes; JPEG brings
each frame down to a few hundred kilobytes, which is what makes a smooth live
view practical. That is exactly the role the architecture reserves for
JPEG/MJPEG -- a streaming and UI format -- so it belongs here at the edge, not in
the perception pipeline, where the detector still works on the raw frame.

The lossless PNG path stays available for when an exact frame matters; this is
the lighter option the live relay defaults to.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from brain.perception.camera_frame import CameraFrame, FrameEncoding


DEFAULT_JPEG_QUALITY = 80


class JpegEncodeError(ValueError):
    """Raised when a frame cannot be encoded as JPEG."""


def encode_rgb8_to_jpeg(data: bytes, width: int, height: int, *, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """Return JPEG bytes for a raw 8-bit RGB buffer, origin top-left."""
    if width <= 0 or height <= 0:
        raise JpegEncodeError("JPEG dimensions must be positive.")
    if len(data) != width * height * 3:
        raise JpegEncodeError("RGB8 data length does not match the dimensions.")
    if not 1 <= quality <= 100:
        raise JpegEncodeError("JPEG quality must be between 1 and 100.")
    image = Image.frombytes("RGB", (width, height), data)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def encode_frame_to_jpeg(frame: CameraFrame, *, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """Encode a raw RGB camera frame to JPEG, refusing anything but RGB8."""
    if frame.encoding is not FrameEncoding.RGB8:
        raise JpegEncodeError(f"JPEG encoding needs an RGB8 frame, not {frame.encoding.value}.")
    if not frame.is_well_formed():
        raise JpegEncodeError("The frame's bytes do not match its dimensions.")
    return encode_rgb8_to_jpeg(frame.data, frame.width, frame.height, quality=quality)


def is_jpeg(data: bytes) -> bool:
    """Whether a byte string begins with the JPEG start-of-image marker."""
    return data[:2] == b"\xff\xd8"
