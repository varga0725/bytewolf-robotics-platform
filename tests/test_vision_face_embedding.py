"""Private ArcFace-compatible 1:1 cosine verification tests."""

from __future__ import annotations

import unittest

from brain.vision.face_embedding import (
    PrivateFaceEmbedding,
    PrivateOneToOneVerifier,
    SimilarityResult,
)


def embedding(value: float = 1.0, *, model_version: str = "r100-v1") -> PrivateFaceEmbedding:
    return PrivateFaceEmbedding("research-arcface", model_version, (value,) + (0.0,) * 511)


class PrivateFaceEmbeddingTests(unittest.TestCase):
    def test_serializes_for_private_encrypted_storage_and_round_trips(self) -> None:
        original = embedding()

        restored = PrivateFaceEmbedding.deserialize(original.serialize())

        self.assertEqual(restored, original)
        self.assertNotIn("embedding", SimilarityResult.__dataclass_fields__)
        self.assertNotIn("template", SimilarityResult.__dataclass_fields__)

    def test_returns_only_a_scalar_for_same_model_1_to_1_comparison(self) -> None:
        result = PrivateOneToOneVerifier().compare(embedding(), embedding())

        self.assertEqual(result.model_id, "research-arcface")
        self.assertEqual(result.model_version, "r100-v1")
        self.assertEqual(result.similarity, 1.0)

    def test_rejects_mismatched_model_or_zero_vector(self) -> None:
        verifier = PrivateOneToOneVerifier()
        with self.assertRaisesRegex(ValueError, "model"):
            verifier.compare(embedding(), embedding(model_version="other"))
        with self.assertRaisesRegex(ValueError, "non-zero"):
            PrivateFaceEmbedding("research-arcface", "r100-v1", (0.0,) * 512)


if __name__ == "__main__":
    unittest.main()
