"""The detector adapter must emit validated data and never a command.

Every result is checked through the same contract that guards it, and the four
states a consumer must tell apart -- valid, invalid, missing, stale -- each get
a test, because acting on the wrong one is the failure this adapter exists to
prevent.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.detector import (
    BoundingBox,
    Detection,
    DetectionContractError,
    DetectorAdapter,
    DetectorState,
    StubDetectorBackend,
    validate_detection_document,
)


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
_PAD = Detection("landing-pad", 0.92, BoundingBox(120, 80, 200, 150))


def _frame(frame_id: str = "frame-1", *, data: bytes = b"\xff\xd8\xff\xd9", width: int = 640, height: int = 480) -> CameraFrame:
    return CameraFrame(
        sensor_id="front_rgb", encoding=FrameEncoding.JPEG, width=width, height=height,
        data=data, captured_at=_NOW, frame_id=frame_id,
    )


def _adapter(detections=None, **kwargs) -> DetectorAdapter:
    backend = StubDetectorBackend(detections or {"frame-1": [_PAD]})
    return DetectorAdapter(backend, **kwargs)


class DetectionStateTests(unittest.TestCase):
    def test_a_detected_object_is_a_valid_actionable_result(self) -> None:
        result = _adapter().analyze(_frame())

        self.assertEqual(result.state(_NOW), DetectorState.VALID)
        self.assertEqual([d.label for d in result.usable_detections(_NOW)], ["landing-pad"])

    def test_no_object_is_a_valid_empty_result_not_an_error(self) -> None:
        result = _adapter().analyze(_frame("empty-scene"))

        self.assertEqual(result.state(_NOW), DetectorState.VALID)
        self.assertEqual(result.usable_detections(_NOW), ())

    def test_a_stale_frame_must_not_be_acted_on(self) -> None:
        result = _adapter(max_age_s=0.5).analyze(_frame())

        later = _NOW + timedelta(seconds=1)
        self.assertEqual(result.state(later), DetectorState.STALE)
        with self.assertRaisesRegex(DetectionContractError, "stale"):
            result.usable_detections(later)

    def test_a_backend_failure_becomes_an_invalid_result_not_an_exception(self) -> None:
        class ExplodingBackend:
            def detect(self, frame):
                raise RuntimeError("model crashed")

        result = DetectorAdapter(ExplodingBackend()).analyze(_frame())

        self.assertEqual(result.state(_NOW), DetectorState.INVALID)
        self.assertEqual(result.detections, ())
        with self.assertRaisesRegex(DetectionContractError, "invalid"):
            result.usable_detections(_NOW)

    def test_a_missing_frame_is_missing_not_empty(self) -> None:
        result = _adapter().analyze(None)

        self.assertEqual(result.state(_NOW), DetectorState.MISSING)
        self.assertEqual(result.detections, ())

    def test_an_unreadable_frame_is_invalid(self) -> None:
        result = _adapter().analyze(_frame(data=b""))

        self.assertEqual(result.state(_NOW), DetectorState.INVALID)


class FailClosedContractTests(unittest.TestCase):
    def test_a_box_outside_the_frame_fails_closed(self) -> None:
        """A backend that returns an off-frame box must not reach a consumer."""
        adapter = _adapter({"frame-1": [Detection("ghost", 0.5, BoundingBox(600, 400, 200, 200))]})

        result = adapter.analyze(_frame())

        self.assertEqual(result.state(_NOW), DetectorState.INVALID)

    def test_an_over_confident_detection_fails_closed(self) -> None:
        adapter = _adapter({"frame-1": [Detection("pad", 1.5, BoundingBox(10, 10, 20, 20))]})

        result = adapter.analyze(_frame())

        self.assertEqual(result.state(_NOW), DetectorState.INVALID)

    def test_an_invalid_result_carries_no_detections(self) -> None:
        """Absence of a payload means a consumer cannot read invalid as 'nothing there'."""
        class ExplodingBackend:
            def detect(self, frame):
                raise ValueError("bad")

        document = DetectorAdapter(ExplodingBackend()).analyze(_frame()).to_document()

        self.assertEqual(document["validity"], "invalid")
        self.assertEqual(document["detections"], [])
        validate_detection_document(document)


class ContractAndDashboardShapeTests(unittest.TestCase):
    def test_the_document_is_schema_valid_and_dashboard_shaped(self) -> None:
        document = _adapter().analyze(_frame()).to_document()

        validate_detection_document(document)
        # The read-only dashboard reads exactly these fields off /api/detections.
        self.assertEqual(document["frame"], {"width": 640, "height": 480, "frame_id": "frame-1"})
        self.assertEqual(document["captured_at"], "2026-07-18T09:00:00Z")
        detection = document["detections"][0]
        self.assertEqual(detection["label"], "landing-pad")
        self.assertEqual(detection["bbox"], {"x": 120, "y": 80, "width": 200, "height": 150})

    def test_the_schema_rejects_a_non_valid_result_that_carries_detections(self) -> None:
        forged = {
            "contract_version": "v0.1",
            "captured_at": "2026-07-18T09:00:00Z",
            "max_age_s": 0.5,
            "validity": "invalid",
            "frame": {"width": 640, "height": 480},
            "detections": [{"label": "x", "confidence": 0.5, "bbox": {"x": 1, "y": 1, "width": 1, "height": 1}}],
        }

        with self.assertRaises(DetectionContractError):
            validate_detection_document(forged)


class ReplaceabilityTests(unittest.TestCase):
    def test_any_backend_matching_the_interface_drops_in(self) -> None:
        """The adapter depends on the detect() interface, not on a specific model."""
        class FixedBackend:
            def detect(self, frame):
                return [Detection("marker", 0.5, BoundingBox(0, 0, 10, 10))]

        result = DetectorAdapter(FixedBackend()).analyze(_frame())

        self.assertEqual([d.label for d in result.usable_detections(_NOW)], ["marker"])

    def test_the_detector_module_imports_no_flight_or_mavsdk_path(self) -> None:
        """Detection is perception, so it must not reach for a way to command."""
        import ast
        from pathlib import Path

        import brain.perception.detector as detector

        tree = ast.parse(Path(detector.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for module in imported:
            self.assertNotIn("mavsdk", module)
            self.assertNotIn("adapters", module)


if __name__ == "__main__":
    unittest.main()
