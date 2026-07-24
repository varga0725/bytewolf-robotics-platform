"""The Vision producer conforms to the shared cross-consumer contracts.

A DetectionEvent and a VisionSummary, produced by the Vision pipeline, serialise
to documents that validate against shared/schemas/vision/*. This is the producer
half of the contract handshake: together with the consumer-side loader on the
functional base branch, it proves the round trip -- Vision writes exactly what a
consumer (the Cognitive Runtime) reads, decoupled from Vision internals.
"""

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import unittest

from brain.vision.canonical_serialize import (
    detection_event_to_canonical,
    vision_summary_to_canonical,
)
from brain.vision.contracts import (
    CAMERA_FRAME_V1,
    BoundingBox,
    CameraFrame,
    Detection,
    DetectionResult,
)
from brain.vision.events import canonical_from_detection_result


SCHEMAS = Path(__file__).resolve().parents[1] / "shared/schemas/vision"
NOW = datetime(2026, 7, 24, 9, 0, tzinfo=UTC)


def _validator(name: str):
    import jsonschema  # imported lazily so the module loads without the dep

    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def _summary() -> "VisionSummary":
    frame = CameraFrame(
        CAMERA_FRAME_V1, "device-1", "front_rgb", "session-1", 1, NOW, NOW,
        "cal-1", "a" * 64, "jpeg", 1280, 720, 12.0, 0,
    )
    result = DetectionResult(
        "detection_result.v1", frame, "yolo", "v8n-aerial-2026-07", NOW,
        (
            Detection("person", 0.91, BoundingBox(320, 180, 96, 220), "t-3"),
            Detection("car", 0.77, BoundingBox(800, 400, 200, 120)),
        ),
    )
    return canonical_from_detection_result(result, ttl=timedelta(seconds=1))


@unittest.skipUnless(
    importlib.util.find_spec("jsonschema"), "jsonschema (a declared dependency) is required to validate the schema"
)
class CanonicalCompatTests(unittest.TestCase):
    def test_a_serialised_summary_validates_against_the_shared_schema(self) -> None:
        document = vision_summary_to_canonical(_summary())
        _validator("vision_summary_v0_1.schema.json").validate(document)
        self.assertEqual(document["source"], "camera:front_rgb")
        self.assertEqual(document["detection_count"], 2)
        self.assertEqual(document["contract_version"], "v0.1")

    def test_a_serialised_detection_event_validates_against_the_shared_schema(self) -> None:
        document = detection_event_to_canonical(_summary().events[0])
        _validator("detection_event_v0_1.schema.json").validate(document)
        self.assertEqual(document["contract_version"], "v0.1")
        self.assertEqual(document["frame"], {"width_px": 1280, "height_px": 720})
        # max_age_s carries the internal TTL for the consumer to resolve freshness.
        self.assertEqual(document["max_age_s"], 1.0)

    def test_a_tracked_detection_carries_its_tracker_id(self) -> None:
        document = vision_summary_to_canonical(_summary())
        person = next(d for d in document["detections"] if d["label"] == "person")
        car = next(d for d in document["detections"] if d["label"] == "car")
        self.assertEqual(person.get("tracker_id"), "t-3")
        self.assertNotIn("tracker_id", car)


if __name__ == "__main__":
    unittest.main()
