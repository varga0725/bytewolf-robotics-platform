from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, DETECTION_RESULT_V1, BoundingBox, CameraFrame, Detection, DetectionResult
from brain.vision.evaluation import EvaluationFrame, GroundTruth, GroundTruthEvaluator, GroundTruthValidationError


NOW = datetime(2026, 7, 21, tzinfo=UTC)


def _result(sequence: int, detections: tuple[Detection, ...], *, latency_ms: float = 10) -> DetectionResult:
    payload = f"frame-{sequence}".encode()
    frame = CameraFrame(
        CAMERA_FRAME_V1, "device", "camera", "session", sequence,
        NOW + timedelta(milliseconds=sequence), NOW + timedelta(milliseconds=sequence + latency_ms),
        "cal-1", sha256(payload).hexdigest(), "jpeg", 100, 100, latency_ms, 0,
    )
    return DetectionResult(DETECTION_RESULT_V1, frame, "stub", "v1", frame.received_at, detections)


def _truth(target_id: str, box: BoundingBox, *, label: str = "person") -> GroundTruth:
    return GroundTruth(target_id, label, box)


class GroundTruthEvaluatorTests(unittest.TestCase):
    def test_per_frame_iou_matching_converts_to_benchmark_sample(self) -> None:
        result = _result(1, (
            Detection("person", .9, BoundingBox(0, 0, 10, 10), "track-a"),
            Detection("person", .8, BoundingBox(40, 40, 10, 10), "track-b"),
            Detection("car", .7, BoundingBox(70, 70, 10, 10), "track-c"),
        ))
        report = GroundTruthEvaluator(iou_threshold=.5).evaluate((
            EvaluationFrame(result, (_truth("one", BoundingBox(0, 0, 10, 10)), _truth("two", BoundingBox(45, 45, 10, 10)))),
        ))

        sample = report.samples[0]
        self.assertEqual((sample.true_positives, sample.false_positives, sample.false_negatives), (1, 2, 1))
        self.assertEqual(sample.latency_ms, 10)
        self.assertEqual(report.reacquisitions, 0)

    def test_tracking_reports_switch_fragmentation_and_reacquisition(self) -> None:
        frames = (
            EvaluationFrame(_result(1, (Detection("person", .9, BoundingBox(0, 0, 10, 10), "a"),)), (_truth("subject", BoundingBox(0, 0, 10, 10)),)),
            EvaluationFrame(_result(2, ()), (_truth("subject", BoundingBox(0, 0, 10, 10)),)),
            EvaluationFrame(_result(3, (Detection("person", .9, BoundingBox(0, 0, 10, 10), "b"),)), (_truth("subject", BoundingBox(0, 0, 10, 10)),)),
        )
        report = GroundTruthEvaluator().evaluate(frames)

        self.assertEqual(report.samples[1].false_negatives, 1)
        self.assertEqual(report.samples[2].fragmentations, 1)
        self.assertEqual(report.samples[2].id_switches, 1)
        self.assertEqual(report.reacquisitions, 1)

    def test_matching_maximizes_true_positives_before_iou_tie_breaks(self) -> None:
        result = _result(1, (
            Detection("person", .9, BoundingBox(0, 0, 10, 10), "a"),
            Detection("person", .9, BoundingBox(5, 0, 10, 10), "b"),
        ))
        report = GroundTruthEvaluator(iou_threshold=.2).evaluate((
            EvaluationFrame(result, (
                _truth("wide", BoundingBox(0, 0, 10, 10)),
                _truth("narrow", BoundingBox(0, 0, 5, 10)),
            )),
        ))
        self.assertEqual(report.samples[0].true_positives, 2)

    def test_ambiguous_or_invalid_ground_truth_fails_closed(self) -> None:
        result = _result(1, ())
        with self.assertRaises(GroundTruthValidationError):
            GroundTruthEvaluator().evaluate((
                EvaluationFrame(result, (_truth("same", BoundingBox(0, 0, 2, 2)), _truth("same", BoundingBox(3, 3, 2, 2)))),
            ))
        with self.assertRaises(GroundTruthValidationError):
            GroundTruthEvaluator().evaluate((
                EvaluationFrame(result, (_truth("subject", BoundingBox(0, 0, 2, 2), label=""),)),
            ))

    def test_duplicate_or_out_of_order_frame_evidence_fails_closed(self) -> None:
        first = EvaluationFrame(_result(2, ()), ())
        second = EvaluationFrame(_result(1, ()), ())
        with self.assertRaises(GroundTruthValidationError):
            GroundTruthEvaluator().evaluate((first, second))


if __name__ == "__main__":
    unittest.main()
