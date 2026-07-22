"""Concrete, payload-integrity-checked Ultralytics YOLO detector adapter.

Weights are supplied by deployment configuration and are deliberately not
downloaded or versioned here.  This adapter has no transport or control path.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
from typing import Any, Protocol

from .contracts import BoundingBox, CameraFrame, Detection


class PayloadResolver(Protocol):
    """Resolve the local image bytes bound to a CameraFrame payload hash."""

    def resolve(self, payload_hash: str) -> bytes: ...


class PayloadIntegrityError(ValueError):
    """The bytes presented to a detector are not the frame that was validated."""


class UltralyticsYoloDetector:
    """Adapt a locally provisioned Ultralytics model to the Vision detector port."""

    def __init__(
        self,
        model_id: str,
        model_version: str,
        resolver: PayloadResolver,
        *,
        weights_path: str | None = None,
        model: Any | None = None,
        decoder: Callable[[bytes], Any] | None = None,
    ) -> None:
        if not model_id or not model_version:
            raise ValueError("YOLO detector requires a model ID and version.")
        if model is None and not weights_path:
            raise ValueError("Provide a provisioned weights path or a model instance.")
        self.model_id = model_id
        self.model_version = model_version
        self._resolver = resolver
        self._model = model if model is not None else _load_model(weights_path)
        self._decoder = decoder or _decode_image

    def detect(self, frame: CameraFrame, _produced_at: datetime) -> tuple[Detection, ...]:
        payload = self._resolver.resolve(frame.payload_hash)
        if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != frame.payload_hash:
            raise PayloadIntegrityError("Resolved image bytes do not match the CameraFrame payload hash.")
        image = self._decoder(payload)
        if image is None:
            raise PayloadIntegrityError("Camera payload cannot be decoded as an image.")
        results = self._model(image, verbose=False)
        if not results:
            return ()
        result = results[0]
        names = getattr(result, "names", getattr(self._model, "names", {}))
        return tuple(_detection_from_box(box, names) for box in _iter_boxes(getattr(result, "boxes", None)))


def _load_model(weights_path: str | None) -> Any:
    try:
        from ultralytics import YOLO
    except ImportError as error:  # pragma: no cover - deployment guard
        raise RuntimeError("Ultralytics is not installed; install the approved research runtime.") from error
    return YOLO(weights_path)


def _decode_image(payload: bytes) -> Any:
    try:
        import cv2
        import numpy
    except ImportError as error:  # pragma: no cover - deployment guard
        raise RuntimeError("OpenCV and NumPy are required for the YOLO image adapter.") from error
    return cv2.imdecode(numpy.frombuffer(payload, dtype=numpy.uint8), cv2.IMREAD_COLOR)


def _iter_boxes(boxes: Any) -> tuple[tuple[float, float, float, float, float, int], ...]:
    if boxes is None:
        return ()
    coordinates = getattr(boxes, "xyxy", ())
    confidences = getattr(boxes, "conf", ())
    classes = getattr(boxes, "cls", ())
    return tuple(
        (float(x1), float(y1), float(x2), float(y2), float(confidence), int(class_id))
        for (x1, y1, x2, y2), confidence, class_id in zip(coordinates, confidences, classes, strict=True)
    )


def _detection_from_box(box: tuple[float, float, float, float, float, int], names: Any) -> Detection:
    x1, y1, x2, y2, confidence, class_id = box
    label = names[class_id] if isinstance(names, dict) else names[class_id]
    return Detection(
        str(label),
        confidence,
        BoundingBox(round(x1), round(y1), max(1, round(x2 - x1)), max(1, round(y2 - y1))),
    )
