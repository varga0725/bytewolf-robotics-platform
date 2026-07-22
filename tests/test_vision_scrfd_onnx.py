"""Explicit-local SCRFD ONNX detector tests for the private P1 research seam."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest

import numpy

import brain.vision.scrfd_onnx as scrfd_module
from brain.vision.scrfd_onnx import ScrfdOnnxDetector
from unittest.mock import patch


class _Input:
    name = "input.1"
    shape = [1, 3, "?", "?"]


class _Output:
    shape = [1, 1]


class _Session:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = outputs
        self.inputs: list[object] = []

    def get_inputs(self):  # type: ignore[no-untyped-def]
        return [_Input()]

    def get_outputs(self):  # type: ignore[no-untyped-def]
        return [_Output()] * 9

    def run(self, _outputs, inputs):  # type: ignore[no-untyped-def]
        self.inputs.append(inputs["input.1"])
        return self._outputs


def outputs(*, scores: tuple[float, ...] = (0.9,)) -> list[object]:
    counts = (32, 8, 2)
    score_outputs = [numpy.zeros((count, 1), dtype=numpy.float32) for count in counts]
    for index, value in zip((0, 2), scores, strict=False):
        score_outputs[0][index, 0] = value
    box_outputs = [numpy.zeros((count, 4), dtype=numpy.float32) for count in counts]
    box_outputs[0][:, :] = (0.0, 0.0, 1.0, 1.0)
    landmark_outputs = [numpy.zeros((count, 10), dtype=numpy.float32) for count in counts]
    landmark_outputs[0][0, :] = (0.0, 0.0, 0.5, 0.0, 0.25, 0.5, 0.0, 0.75, 0.5, 0.75)
    landmark_outputs[0][2, :] = (0.0, 0.0, 0.5, 0.0, 0.25, 0.5, 0.0, 0.75, 0.5, 0.75)
    return [*score_outputs, *box_outputs, *landmark_outputs]


def _write_verified_model(directory: str) -> tuple[Path, str]:
    model = Path(directory) / "det_10g.onnx"
    model.write_bytes(b"research-only-model")
    return model, hashlib.sha256(model.read_bytes()).hexdigest()


class ScrfdOnnxDetectorTests(unittest.TestCase):
    def _detector(self, model: Path, digest: str, session: _Session) -> ScrfdOnnxDetector:
        with patch.object(scrfd_module, "_load_session", return_value=session):
            return ScrfdOnnxDetector(
                model_id="research-scrfd-10gf", model_version="buffalo-l-v0.7",
                model_path=model, expected_sha256=digest, input_size=(32, 32),
            )

    def test_requires_hash_verified_existing_local_model(self) -> None:
        with TemporaryDirectory() as temporary:
            model, digest = _write_verified_model(temporary)

            detector = self._detector(model, digest, _Session(outputs()))

            self.assertEqual(detector.model_id, "research-scrfd-10gf")
            with self.assertRaisesRegex(ValueError, "hash"):
                ScrfdOnnxDetector(
                    model_id="research-scrfd-10gf", model_version="buffalo-l-v0.7",
                    model_path=model, expected_sha256="0" * 64, input_size=(32, 32),
                )
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ScrfdOnnxDetector(
                    model_id="research-scrfd-10gf", model_version="buffalo-l-v0.7",
                    input_size=(32, 32),
                )

    def test_returns_exactly_one_valid_candidate_and_preprocesses_bgr(self) -> None:
        with TemporaryDirectory() as temporary:
            model, digest = _write_verified_model(temporary)
            session = _Session(outputs())
            detector = self._detector(model, digest, session)

            face = detector.detect_single_bgr(numpy.full((20, 16, 3), (10, 20, 30), dtype=numpy.uint8))

            self.assertIsNotNone(face)
            assert face is not None
            self.assertEqual(face.model_id, "research-scrfd-10gf")
            self.assertEqual(len(face.landmarks_xy), 5)
            self.assertEqual(session.inputs[0].shape, (1, 3, 32, 32))
            self.assertAlmostEqual(float(session.inputs[0][0, 0, 0, 0]), (30 - 127.5) / 128.0)

    def test_rejects_ambiguous_or_malformed_model_output_fail_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            model, digest = _write_verified_model(temporary)
            image = numpy.zeros((32, 32, 3), dtype=numpy.uint8)
            ambiguous = self._detector(model, digest, _Session(outputs(scores=(0.9, 0.8))))
            malformed = self._detector(model, digest, _Session([numpy.zeros((1, 1), dtype=numpy.float32)]))

            self.assertIsNone(ambiguous.detect_single_bgr(image))
            self.assertIsNone(malformed.detect_single_bgr(image))
            with self.assertRaisesRegex(ValueError, "uint8 BGR"):
                ambiguous.detect_single_bgr(numpy.zeros((32, 32), dtype=numpy.uint8))
