"""Deterministic local tracker behavior for the observation-only Vision Core."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, BoundingBox, CameraFrame, Detection, VisionContractError
from brain.vision.tracking import IoUAssociationTracker


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def frame(sequence: int) -> CameraFrame:
    return CameraFrame(
        contract_version=CAMERA_FRAME_V1,
        device_id="sim-01",
        camera_id="front-rgb",
        stream_session_id="run-1",
        frame_sequence=sequence,
        captured_at=NOW - timedelta(milliseconds=5),
        received_at=NOW,
        calibration_version="v1",
        payload_hash=f"{sequence:064x}",
        encoding="jpeg",
        width_px=1280,
        height_px=720,
        latency_ms=5.0,
        dropped_frames=0,
    )


def person(x: int = 10, y: int = 10, width: int = 40, height: int = 80) -> Detection:
    return Detection("person", 0.9, BoundingBox(x, y, width, height))


class IoUAssociationTrackerTests(unittest.TestCase):
    def test_retains_an_opaque_id_for_an_overlapping_observation(self) -> None:
        tracker = IoUAssociationTracker(iou_threshold=0.5)

        first = tracker.track((person(),), frame(1))
        second = tracker.track((person(14, 10),), frame(2))

        self.assertEqual(first[0].tracker_id, "local-000001")
        self.assertEqual(second[0].tracker_id, first[0].tracker_id)
        self.assertEqual(second[0].bounding_box, BoundingBox(14, 10, 40, 80))

    def test_distinct_objects_and_labels_receive_distinct_ids(self) -> None:
        tracker = IoUAssociationTracker(iou_threshold=0.1)

        tracked = tracker.track((person(), Detection("vehicle", 0.8, BoundingBox(10, 10, 40, 80))), frame(1))

        self.assertEqual(tuple(item.tracker_id for item in tracked), ("local-000001", "local-000002"))

    def test_expiration_is_explicit_and_reacquisition_gets_a_new_id(self) -> None:
        tracker = IoUAssociationTracker(maximum_missed_frames=1)
        first = tracker.track((person(),), frame(1))
        tracker.track((), frame(2))
        tracker.track((), frame(3))

        reacquired = tracker.track((person(),), frame(4))

        self.assertEqual(first[0].tracker_id, "local-000001")
        self.assertEqual(reacquired[0].tracker_id, "local-000002")
        self.assertEqual(tracker.expired_track_ids, ())

    def test_reports_expiration_on_the_call_that_removes_the_track(self) -> None:
        tracker = IoUAssociationTracker(maximum_missed_frames=0)
        initial = tracker.track((person(),), frame(1))

        tracker.track((), frame(2))

        self.assertEqual(tracker.expired_track_ids, (initial[0].tracker_id,))

    def test_assignment_is_deterministic_when_multiple_tracks_overlap(self) -> None:
        tracker = IoUAssociationTracker(iou_threshold=0.1)
        first = tracker.track((person(0), person(20)), frame(1))

        second = tracker.track((person(10), person(20)), frame(2))

        self.assertEqual(tuple(item.tracker_id for item in first), ("local-000001", "local-000002"))
        self.assertEqual(tuple(item.tracker_id for item in second), ("local-000001", "local-000002"))

    def test_rejects_non_observation_or_out_of_frame_detections(self) -> None:
        tracker = IoUAssociationTracker()

        with self.assertRaisesRegex(VisionContractError, "Detection contract"):
            tracker.track((object(),), frame(1))  # type: ignore[arg-type]
        with self.assertRaisesRegex(VisionContractError, "exceeds source frame"):
            tracker.track((person(1270, 0),), frame(1))
        with self.assertRaisesRegex(VisionContractError, "unassigned"):
            tracker.track((Detection("person", 0.9, BoundingBox(0, 0, 10, 10), "external-1"),), frame(1))

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(VisionContractError, "IoU threshold"):
            IoUAssociationTracker(iou_threshold=1.1)
        with self.assertRaisesRegex(VisionContractError, "IoU threshold"):
            IoUAssociationTracker(iou_threshold=0.0)
        with self.assertRaisesRegex(VisionContractError, "maximum_missed_frames"):
            IoUAssociationTracker(maximum_missed_frames=-1)


if __name__ == "__main__":
    unittest.main()
