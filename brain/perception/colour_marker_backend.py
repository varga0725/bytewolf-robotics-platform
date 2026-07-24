"""A content-based detector backend that finds a coloured marker in a frame.

This is a real detector, not the stub: it reads the pixels and locates a marker
of a known colour, returning a detection at its centroid. It is deliberately
simple -- a colour threshold and a centroid -- because its job is to be an honest,
dependency-free way to exercise the whole perception path on a real camera frame,
and to anchor the target estimator's geometry against ground truth. A learned
backend replaces it behind the same ``DetectorBackend`` interface.

It only understands raw RGB, and refuses any other encoding rather than guessing,
so a depth or compressed frame fails closed through the adapter instead of
producing a detection from bytes it cannot read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.detector import BoundingBox, Detection


class ColourMarkerBackendError(ValueError):
    """Raised when a frame cannot be searched for the marker."""


@dataclass(frozen=True)
class ColourTarget:
    """The marker colour to look for, and how close a pixel must be to count."""

    red: int
    green: int
    blue: int
    tolerance: int = 60


class ColourMarkerBackend:
    """Find the centroid of the most marker-coloured region in an RGB frame."""

    def __init__(
        self,
        target: ColourTarget,
        *,
        label: str = "marker",
        min_pixels: int = 40,
        sample_step: int = 2,
    ) -> None:
        self._target = target
        self._label = label
        self._min_pixels = min_pixels
        self._sample_step = max(1, sample_step)

    def detect(self, frame: CameraFrame) -> Sequence[Detection]:
        if frame.encoding is not FrameEncoding.RGB8:
            raise ColourMarkerBackendError(
                f"The colour marker backend reads RGB8 only, not {frame.encoding.value}."
            )
        match = _find_centroid(
            frame.data, frame.width, frame.height, self._target, self._sample_step
        )
        if match is None or match.count < self._min_pixels:
            return ()
        # A small box around the centroid; the estimator uses its centre, so the
        # exact size does not matter, only that it stays inside the frame.
        half = 15.0
        x = min(max(match.centre_u - half, 0.0), frame.width - 2 * half)
        y = min(max(match.centre_v - half, 0.0), frame.height - 2 * half)
        confidence = min(1.0, match.count / (match.count + _CONFIDENCE_SOFTENING))
        return (Detection(self._label, round(confidence, 4), BoundingBox(x, y, 2 * half, 2 * half)),)


# A blob of a few hundred matched pixels reads as a confident detection; a
# handful reads as a weak one. This softens the count into [0, 1) monotonically.
_CONFIDENCE_SOFTENING = 200.0


@dataclass(frozen=True)
class _CentroidMatch:
    centre_u: float
    centre_v: float
    count: int


def _find_centroid(
    data: bytes, width: int, height: int, target: ColourTarget, sample_step: int
) -> _CentroidMatch | None:
    if len(data) != width * height * 3:
        raise ColourMarkerBackendError("RGB8 frame data does not match its declared dimensions.")
    sum_u = 0
    sum_v = 0
    count = 0
    tolerance = target.tolerance
    for v in range(0, height, sample_step):
        row = v * width * 3
        for u in range(0, width, sample_step):
            index = row + u * 3
            if (
                abs(data[index] - target.red) <= tolerance
                and abs(data[index + 1] - target.green) <= tolerance
                and abs(data[index + 2] - target.blue) <= tolerance
            ):
                sum_u += u
                sum_v += v
                count += 1
    if count == 0:
        return None
    return _CentroidMatch(sum_u / count, sum_v / count, count)
