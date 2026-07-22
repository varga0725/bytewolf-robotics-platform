from __future__ import annotations

import unittest

from brain.vision.models import ModelRecord, ModelRegistry, ModelStage


class VisionModelRegistryTests(unittest.TestCase):
    def test_research_and_production_models_are_explicitly_separated(self) -> None:
        registry = ModelRegistry((
            ModelRecord("yolo-research", "v1", ModelStage.RESEARCH, weights_path="/models/yolo.pt"),
            ModelRecord("detector-production", "v2", ModelStage.PRODUCTION, weights_path="/models/detector.engine", license_reference="PROC-42"),
        ))

        self.assertEqual(registry.resolve("yolo-research", public_release=False).stage, ModelStage.RESEARCH)
        self.assertEqual(registry.resolve("detector-production", public_release=True).license_reference, "PROC-42")
        with self.assertRaisesRegex(ValueError, "research"):
            registry.resolve("yolo-research", public_release=True)

    def test_production_record_requires_a_license_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "license"):
            ModelRecord("production", "v1", ModelStage.PRODUCTION, weights_path="/models/model", license_reference=None)


if __name__ == "__main__":
    unittest.main()
