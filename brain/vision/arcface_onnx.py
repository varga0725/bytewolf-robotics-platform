"""Explicit-local ONNX ArcFace adapter for the private P1 research path.

The adapter accepts only an already aligned in-memory crop and returns a
``PrivateFaceEmbedding``. It does not download weights, detect faces, retain
pixels, or expose embeddings outside the private verification boundary.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .face_embedding import PrivateFaceEmbedding


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ArcFaceOnnxEmbedder:
    """Run a provisioned ArcFace ONNX model on a 112×112 BGR aligned crop."""

    def __init__(
        self,
        *,
        model_id: str,
        model_version: str,
        model_path: Path | None = None,
        expected_sha256: str | None = None,
        session: Any | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("ArcFace ONNX adapter requires model ID and version.")
        _verify_model(model_path, expected_sha256)
        if session is None:
            try:
                import onnxruntime
            except ImportError as error:  # pragma: no cover - deployment guard
                raise RuntimeError("ArcFace ONNX adapter requires onnxruntime in the research runtime.") from error
            session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        get_inputs = getattr(session, "get_inputs", None)
        if not callable(get_inputs):
            raise ValueError("ArcFace ONNX session must expose get_inputs().")
        inputs = get_inputs()
        if not isinstance(inputs, (list, tuple)) or len(inputs) != 1 or not isinstance(getattr(inputs[0], "name", None), str):
            raise ValueError("ArcFace ONNX session must expose exactly one named input.")
        if not callable(getattr(session, "run", None)):
            raise ValueError("ArcFace ONNX session must expose run().")
        self.model_id = model_id
        self.model_version = model_version
        self._session = session
        self._input_name = inputs[0].name

    def embed_aligned_bgr(self, image: object) -> PrivateFaceEmbedding:
        """Return a private normalized 128/512-D vector from one aligned BGR crop."""
        try:
            import numpy
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError("ArcFace ONNX adapter requires NumPy in the research runtime.") from error
        if not isinstance(image, numpy.ndarray) or image.dtype != numpy.uint8:
            raise ValueError("ArcFace ONNX adapter requires a uint8 BGR image.")
        if image.shape != (112, 112, 3):
            raise ValueError("ArcFace ONNX adapter requires an aligned 112x112x3 BGR image.")
        # ArcFace's usual preprocessing is BGR→RGB then (pixel-127.5)/127.5.
        tensor = (image[:, :, ::-1].astype(numpy.float32) - 127.5) / 127.5
        tensor = numpy.transpose(tensor, (2, 0, 1))[numpy.newaxis, :, :, :]
        try:
            outputs = self._session.run(None, {self._input_name: tensor})
            if not isinstance(outputs, (list, tuple)) or len(outputs) != 1:
                raise ValueError("ArcFace ONNX model must return exactly one embedding output.")
            values = tuple(float(value) for value in numpy.asarray(outputs[0]).reshape(-1))
        except ValueError:
            raise
        except Exception as error:
            raise RuntimeError("ArcFace ONNX inference failed.") from error
        return PrivateFaceEmbedding(self.model_id, self.model_version, values)


def _verify_model(path: Path | None, expected_sha256: str | None) -> None:
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("ArcFace ONNX adapter requires an existing local model_path; downloads are disabled.")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError("ArcFace ONNX adapter requires a lowercase SHA-256 hash.")
    if sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("ArcFace ONNX local model hash does not match the approved hash.")
