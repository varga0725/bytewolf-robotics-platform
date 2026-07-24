"""Encode a raw RGB frame to PNG with the standard library alone.

The simulator's camera publishes raw RGB, and a browser needs a compressed image
to display. PNG is the honest choice for that here: it is lossless, so what the
dashboard shows is exactly what the detector saw, and it needs no dependency --
``zlib`` is in the standard library, and the rest of the format is a handful of
length-prefixed, CRC-checked chunks. JPEG/MJPEG stays a streaming and UI concern,
never the basis of the pipeline, so nothing downstream is tied to it.

This encodes 8-bit truecolour only, which is what the camera frame carries.
"""

from __future__ import annotations

import struct
import zlib

from brain.perception.camera_frame import CameraFrame, FrameEncoding


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngEncodeError(ValueError):
    """Raised when a frame cannot be encoded as PNG."""


def encode_rgb8_to_png(data: bytes, width: int, height: int) -> bytes:
    """Return PNG bytes for a raw 8-bit RGB buffer, origin top-left."""
    if width <= 0 or height <= 0:
        raise PngEncodeError("PNG dimensions must be positive.")
    if len(data) != width * height * 3:
        raise PngEncodeError("RGB8 data length does not match the dimensions.")

    # Each scanline is prefixed with filter type 0 (None); the raw bytes follow.
    stride = width * 3
    raw = bytearray()
    for row_start in range(0, len(data), stride):
        raw.append(0)
        raw.extend(data[row_start : row_start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            _PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", zlib.compress(bytes(raw), 6)),
            _chunk(b"IEND", b""),
        )
    )


def encode_frame_to_png(frame: CameraFrame) -> bytes:
    """Encode a raw RGB camera frame to PNG, refusing anything but RGB8."""
    if frame.encoding is not FrameEncoding.RGB8:
        raise PngEncodeError(f"PNG encoding needs an RGB8 frame, not {frame.encoding.value}.")
    if not frame.is_well_formed():
        raise PngEncodeError("The frame's bytes do not match its dimensions.")
    return encode_rgb8_to_png(frame.data, frame.width, frame.height)


def is_png(data: bytes) -> bool:
    """Whether a byte string begins with the PNG signature."""
    return data[:8] == _PNG_SIGNATURE


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return b"".join(
        (
            struct.pack(">I", len(payload)),
            kind,
            payload,
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF),
        )
    )
