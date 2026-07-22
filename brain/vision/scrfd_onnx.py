"""Explicit-local SCRFD-10GF ONNX adapter for private P1 face research.

This module accepts only a manually provisioned, SHA-256-bound model artifact.
It neither downloads a model nor returns an arbitrary face from a multi-face
frame: zero, malformed, or ambiguous candidates return ``None``.
"""

from __future__ import annotations

from hashlib import sha256
from math import isfinite
from pathlib import Path
import re
from typing import Any

from .face_alignment import ScrfdFaceCandidate


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STRIDES = (8, 16, 32)


class ScrfdOnnxDetector:
    """Run a local three-stride, five-landmark SCRFD ONNX detector in memory."""

    def __init__(
        self,
        *,
        model_id: str,
        model_version: str,
        model_path: Path | None = None,
        expected_sha256: str | None = None,
        session: Any | None = None,
        input_size: tuple[int, int] = (640, 640),
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.4,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("SCRFD ONNX adapter requires model ID and version.")
        if not _dimensions(input_size) or not _unit_score(confidence_threshold) or not _unit_score(nms_threshold):
            raise ValueError("SCRFD input size and thresholds are invalid.")
        _verify_model(model_path, expected_sha256)
        if session is None:
            try:
                import onnxruntime
            except ImportError as error:  # pragma: no cover - deployment guard
                raise RuntimeError("SCRFD ONNX adapter requires onnxruntime in the research runtime.") from error
            session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        inputs = getattr(session, "get_inputs", lambda: None)()
        outputs = getattr(session, "get_outputs", lambda: None)()
        if not isinstance(inputs, (list, tuple)) or len(inputs) != 1 or not isinstance(getattr(inputs[0], "name", None), str):
            raise ValueError("SCRFD ONNX session must expose exactly one named input.")
        if not isinstance(outputs, (list, tuple)) or len(outputs) != 9 or not callable(getattr(session, "run", None)):
            raise ValueError("SCRFD ONNX session must expose nine outputs and run().")
        self.model_id = model_id
        self.model_version = model_version
        self._session = session
        self._input_name = inputs[0].name
        self._input_size = input_size
        self._confidence_threshold = float(confidence_threshold)
        self._nms_threshold = float(nms_threshold)

    def detect_single_bgr(self, image: object) -> ScrfdFaceCandidate | None:
        """Return one unambiguous candidate, otherwise fail closed with ``None``."""
        try:
            import cv2
            import numpy
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError("SCRFD ONNX adapter requires OpenCV and NumPy in the research runtime.") from error
        if not isinstance(image, numpy.ndarray) or image.dtype != numpy.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
            raise ValueError("SCRFD ONNX adapter requires a non-empty uint8 BGR image.")
        try:
            prepared, scale = _prepare(image, self._input_size, cv2, numpy)
            raw_outputs = self._session.run(None, {self._input_name: prepared})
            candidates = _decode_candidates(
                raw_outputs, self._input_size, image.shape[:2], scale, self._confidence_threshold, self.model_id, self.model_version, numpy,
            )
            survivors = _nms(candidates, self._nms_threshold)
        except Exception:
            return None
        return survivors[0] if len(survivors) == 1 else None


def _verify_model(path: Path | None, expected_sha256: str | None) -> None:
    if not isinstance(path, Path) or not path.is_file() or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError("SCRFD ONNX adapter requires a local model and lowercase SHA-256 hash.")
    digest = sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError("SCRFD ONNX local model hash does not match the approved hash.")


def _prepare(image: Any, input_size: tuple[int, int], cv2: Any, numpy: Any) -> tuple[Any, float]:
    width, height = input_size
    scale = min(width / image.shape[1], height / image.shape[0])
    resized_width, resized_height = max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale))
    canvas = numpy.zeros((height, width, 3), dtype=numpy.uint8)
    canvas[:resized_height, :resized_width] = cv2.resize(image, (resized_width, resized_height))
    blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (width, height), (127.5, 127.5, 127.5), swapRB=True)
    return blob, scale


def _decode_candidates(
    outputs: object, input_size: tuple[int, int], image_shape: tuple[int, int], scale: float,
    threshold: float, model_id: str, model_version: str, numpy: Any,
) -> tuple[ScrfdFaceCandidate, ...]:
    if not isinstance(outputs, (list, tuple)) or len(outputs) != 9:
        return ()
    width, height = input_size
    candidates: list[ScrfdFaceCandidate] = []
    for index, stride in enumerate(_STRIDES):
        rows = (height // stride) * (width // stride) * 2
        scores = numpy.asarray(outputs[index]).reshape(-1)
        boxes = numpy.asarray(outputs[index + 3])
        landmarks = numpy.asarray(outputs[index + 6])
        if scores.size != rows or boxes.shape != (rows, 4) or landmarks.shape != (rows, 10):
            return ()
        centers = _centers(width // stride, height // stride, stride, numpy)
        for row in numpy.where(scores >= threshold)[0]:
            score = float(scores[row])
            values = boxes[row] * stride
            points = landmarks[row].reshape(5, 2) * stride
            xyxy = (centers[row, 0] - values[0], centers[row, 1] - values[1], centers[row, 0] + values[2], centers[row, 1] + values[3])
            points = points + centers[row]
            candidate = _candidate_if_inside(model_id, model_version, score, xyxy, points, image_shape, scale)
            if candidate is not None:
                candidates.append(candidate)
    return tuple(candidates)


def _centers(columns: int, rows: int, stride: int, numpy: Any) -> Any:
    grid = numpy.stack(numpy.mgrid[:rows, :columns][::-1], axis=-1).astype(numpy.float32) * stride
    return numpy.repeat(grid.reshape(-1, 2), 2, axis=0)


def _candidate_if_inside(model_id: str, model_version: str, score: float, xyxy: Any, points: Any, image_shape: tuple[int, int], scale: float) -> ScrfdFaceCandidate | None:
    values = tuple(float(value) / scale for value in xyxy)
    landmarks = tuple((float(point[0]) / scale, float(point[1]) / scale) for point in points)
    image_height, image_width = image_shape
    if not all(isfinite(value) for value in (*values, *(coordinate for point in landmarks for coordinate in point))):
        return None
    if values[0] < 0 or values[1] < 0 or values[2] > image_width or values[3] > image_height:
        return None
    if any(x < 0 or y < 0 or x >= image_width or y >= image_height for x, y in landmarks):
        return None
    try:
        return ScrfdFaceCandidate(model_id, model_version, score, values, landmarks)
    except ValueError:
        return None


def _nms(candidates: tuple[ScrfdFaceCandidate, ...], threshold: float) -> tuple[ScrfdFaceCandidate, ...]:
    remaining = sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)
    kept: list[ScrfdFaceCandidate] = []
    while remaining:
        selected = remaining.pop(0)
        kept.append(selected)
        remaining = [candidate for candidate in remaining if _iou(selected.bounds_xyxy, candidate.bounds_xyxy) <= threshold]
    return tuple(kept)


def _iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    overlap_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    overlap_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    overlap = overlap_width * overlap_height
    total = (left[2] - left[0]) * (left[3] - left[1]) + (right[2] - right[0]) * (right[3] - right[1]) - overlap
    return 0.0 if total <= 0 else overlap / total


def _dimensions(value: object) -> bool:
    return isinstance(value, tuple) and len(value) == 2 and all(type(item) is int and item > 0 and item % 32 == 0 for item in value)


def _unit_score(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and 0.0 <= value <= 1.0
