"""Optional research-runtime renderer for read-only Vision overlays."""

from __future__ import annotations

from .contracts import DetectionResult


class OverlayRenderError(ValueError):
    """A source payload cannot be safely published as a dashboard image."""


def render_jpeg_overlay(payload: bytes, result: DetectionResult | None) -> bytes:
    """Render detection boxes and track IDs into a newly encoded JPEG image."""
    try:
        import cv2
        import numpy
    except ImportError as error:  # pragma: no cover - deployment guard
        raise OverlayRenderError("OpenCV and NumPy are required to render the Vision overlay.") from error
    image = cv2.imdecode(numpy.frombuffer(payload, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise OverlayRenderError("Vision dashboard rendering requires a decodable JPEG source frame.")
    if result is not None:
        for detection in result.detections:
            box = detection.bounding_box
            cv2.rectangle(image, (box.x_px, box.y_px), (box.x_px + box.width_px, box.y_px + box.height_px), (0, 255, 0), 2)
            track = detection.tracker_id or "untracked"
            cv2.putText(image, f"{detection.label} {detection.confidence:.2f} {track}", (box.x_px, max(12, box.y_px - 4)), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 255, 0), 1)
    encoded, jpeg = cv2.imencode(".jpg", image)
    if not encoded:
        raise OverlayRenderError("OpenCV could not encode the Vision overlay as JPEG.")
    return bytes(jpeg)
