"""Explicit-local ONNX ArcFace research adapter tests."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

import numpy

from brain.vision.arcface_onnx import ArcFaceOnnxEmbedder


class _Input:
    name = "input"


class _Session:
    def __init__(self) -> None:
        self.inputs: list[object] = []

    def get_inputs(self):  # type: ignore[no-untyped-def]
        return [_Input()]

    def run(self, _outputs, inputs):  # type: ignore[no-untyped-def]
        self.inputs.append(inputs["input"])
        return [numpy.array([[1.0] + [0.0] * 511], dtype=numpy.float32)]


class ArcFaceOnnxEmbedderTests(unittest.TestCase):
    def _model(self, directory: str) -> tuple[Path, str]:
        path = Path(directory) / "w600k_r50.onnx"
        path.write_bytes(b"research-only-arcface")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_converts_aligned_bgr_crop_to_private_normalized_embedding(self) -> None:
        with TemporaryDirectory() as temporary:
            model, digest = self._model(temporary)
            session = _Session()
            embedder = ArcFaceOnnxEmbedder(
                model_id="research-arcface", model_version="r100-v1", model_path=model, expected_sha256=digest, session=session,
            )
            image = numpy.full((112, 112, 3), (10, 20, 30), dtype=numpy.uint8)

            result = embedder.embed_aligned_bgr(image)

            self.assertEqual(result.model_id, "research-arcface")
            self.assertEqual(len(result.values), 512)
            self.assertEqual(result.values[0], 1.0)
            tensor = session.inputs[0]
            self.assertEqual(tensor.shape, (1, 3, 112, 112))
            self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), (30 - 127.5) / 127.5)

    def test_requires_existing_explicit_local_model_when_no_session_is_injected(self) -> None:
        with TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.arcface.onnx"
            with self.assertRaisesRegex(ValueError, "existing local"):
                ArcFaceOnnxEmbedder(model_id="research-arcface", model_version="r100-v1", model_path=missing, expected_sha256="0" * 64)

    def test_rejects_wrong_shape_or_non_image_input(self) -> None:
        with TemporaryDirectory() as temporary:
            model, digest = self._model(temporary)
            embedder = ArcFaceOnnxEmbedder(model_id="research-arcface", model_version="r100-v1", model_path=model, expected_sha256=digest, session=_Session())
            with self.assertRaisesRegex(ValueError, "112x112"):
                embedder.embed_aligned_bgr(numpy.zeros((32, 32, 3), dtype=numpy.uint8))
            with self.assertRaisesRegex(ValueError, "uint8"):
                embedder.embed_aligned_bgr(numpy.zeros((112, 112, 3), dtype=numpy.float32))

    def test_rejects_injected_session_when_model_hash_is_missing_or_wrong(self) -> None:
        with TemporaryDirectory() as temporary:
            model, _digest = self._model(temporary)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ArcFaceOnnxEmbedder(model_id="research-arcface", model_version="r100-v1", model_path=model, session=_Session())
            with self.assertRaisesRegex(ValueError, "hash"):
                ArcFaceOnnxEmbedder(model_id="research-arcface", model_version="r100-v1", model_path=model, expected_sha256="0" * 64, session=_Session())


if __name__ == "__main__":
    unittest.main()
