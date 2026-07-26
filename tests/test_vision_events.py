"""Coverage for the canonical Vision observation events.

The events are observation-only and self-validating, so most of these tests are
about refusal: a wrong contract version, an empty field, an out-of-range
confidence, a box outside the frame, a non-positive TTL, a naive timestamp, or an
artifact that belongs to a different frame. The rest cover freshness (a valid
event goes stale past its TTL) and the pipeline-to-canonical adapter.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.contracts import (
    CAMERA_FRAME_V1,
    BoundingBox,
    CameraFrame,
    Detection,
    DetectionResult,
    ResultState,
    VisionContractError,
)
from brain.vision.events import (
    DetectionEvent,
    TrackedObject,
    VideoArtifactRef,
    VisionSummary,
    canonical_from_detection_result,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
TTL = timedelta(seconds=1)


def _frame(sequence: int = 1, payload: str = "a" * 64) -> CameraFrame:
    return CameraFrame(CAMERA_FRAME_V1, "d", "c", "s", sequence, NOW, NOW, "cal", payload, "jpeg", 10, 10, 0, 0)


def _artifact(frame: CameraFrame) -> VideoArtifactRef:
    return VideoArtifactRef("video_artifact_ref.v1", "clip", frame, "jpeg", frame.payload_hash, NOW, TTL)


def _event_kwargs(**overrides) -> dict:
    frame = overrides.pop("source_frame", _frame())
    kwargs = {
        "contract_version": "detection_event.v1",
        "event_id": "event-1",
        "source_frame": frame,
        "model_id": "yolo",
        "model_version": "v8",
        "label": "person",
        "confidence": 0.9,
        "bounding_box": BoundingBox(1, 1, 2, 2),
        "observed_at": NOW,
        "ttl": TTL,
    }
    kwargs.update(overrides)
    return kwargs


class VideoArtifactRefTests(unittest.TestCase):
    def test_a_well_formed_artifact_is_accepted(self) -> None:
        _artifact(_frame())

    def test_it_rejects_a_wrong_contract_version(self) -> None:
        with self.assertRaises(VisionContractError):
            VideoArtifactRef("video_artifact_ref.v2", "clip", _frame(), "jpeg", "a" * 64, NOW, TTL)

    def test_it_rejects_an_unknown_encoding(self) -> None:
        with self.assertRaises(VisionContractError):
            VideoArtifactRef("video_artifact_ref.v1", "clip", _frame(), "webm", "a" * 64, NOW, TTL)

    def test_it_rejects_a_hash_that_does_not_match_the_frame(self) -> None:
        with self.assertRaises(VisionContractError):
            VideoArtifactRef("video_artifact_ref.v1", "clip", _frame(payload="a" * 64), "jpeg", "b" * 64, NOW, TTL)

    def test_it_rejects_a_naive_created_at(self) -> None:
        with self.assertRaises(VisionContractError):
            VideoArtifactRef("video_artifact_ref.v1", "clip", _frame(), "jpeg", "a" * 64, datetime(2026, 7, 23), TTL)


class DetectionEventTests(unittest.TestCase):
    def test_a_well_formed_event_is_accepted(self) -> None:
        DetectionEvent(**_event_kwargs())

    def test_it_rejects_a_wrong_contract_version(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs(contract_version="detection_event.v2"))

    def test_it_rejects_empty_text_fields(self) -> None:
        for field in ("event_id", "model_id", "model_version", "label"):
            with self.subTest(field=field):
                with self.assertRaises(VisionContractError):
                    DetectionEvent(**_event_kwargs(**{field: "  "}))

    def test_it_rejects_a_confidence_outside_the_unit_interval(self) -> None:
        for value in (-0.1, 1.1):
            with self.subTest(confidence=value):
                with self.assertRaises(VisionContractError):
                    DetectionEvent(**_event_kwargs(confidence=value))

    def test_it_rejects_a_box_outside_the_source_frame(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs(bounding_box=BoundingBox(9, 9, 2, 2)))

    def test_it_rejects_a_non_positive_ttl(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs(ttl=timedelta(0)))

    def test_it_rejects_a_naive_observed_at(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs(observed_at=datetime(2026, 7, 23)))

    def test_it_rejects_an_artifact_from_another_frame(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs(artifact=_artifact(_frame(sequence=2, payload="b" * 64))))

    def test_a_matching_artifact_is_accepted(self) -> None:
        frame = _frame()
        DetectionEvent(**_event_kwargs(source_frame=frame, artifact=_artifact(frame)))

    def test_freshness_goes_stale_past_the_ttl(self) -> None:
        event = DetectionEvent(**_event_kwargs())
        self.assertEqual(event.state(NOW + timedelta(seconds=0.5)), ResultState.VALID)
        self.assertEqual(event.state(NOW + timedelta(seconds=2)), ResultState.STALE)

    def test_freshness_rejects_a_naive_now(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionEvent(**_event_kwargs()).state(datetime(2026, 7, 23))


class TrackedObjectTests(unittest.TestCase):
    def _kwargs(self, **overrides) -> dict:
        frame = overrides.pop("source_frame", _frame())
        kwargs = {
            "contract_version": "tracked_object.v1", "source_frame": frame, "tracker_id": "t-1",
            "model_id": "yolo", "model_version": "v8", "label": "person", "confidence": 0.9,
            "bounding_box": BoundingBox(1, 1, 2, 2), "observed_at": NOW, "ttl": TTL,
        }
        kwargs.update(overrides)
        return kwargs

    def test_a_well_formed_track_is_accepted(self) -> None:
        TrackedObject(**self._kwargs())

    def test_it_rejects_an_empty_tracker_id(self) -> None:
        with self.assertRaises(VisionContractError):
            TrackedObject(**self._kwargs(tracker_id=""))

    def test_it_rejects_an_artifact_from_another_frame(self) -> None:
        with self.assertRaises(VisionContractError):
            TrackedObject(**self._kwargs(artifact=_artifact(_frame(sequence=2, payload="b" * 64))))

    def test_freshness_goes_stale_past_the_ttl(self) -> None:
        track = TrackedObject(**self._kwargs())
        self.assertEqual(track.state(NOW + timedelta(seconds=2)), ResultState.STALE)


class VisionSummaryTests(unittest.TestCase):
    def _summary(self, **overrides) -> VisionSummary:
        kwargs = {
            "contract_version": "vision_summary.v1", "source_frame": _frame(), "model_id": "yolo",
            "model_version": "v8", "observed_at": NOW, "ttl": TTL, "state_value": ResultState.VALID,
            "events": (), "tracks": (),
        }
        kwargs.update(overrides)
        return VisionSummary(**kwargs)

    def test_a_well_formed_summary_is_accepted(self) -> None:
        self._summary()

    def test_it_rejects_non_tuple_collections(self) -> None:
        with self.assertRaises(VisionContractError):
            self._summary(events=[])

    def test_a_producer_declared_non_valid_state_is_kept(self) -> None:
        summary = self._summary(state_value=ResultState.MISSING)
        self.assertEqual(summary.state(NOW), ResultState.MISSING)

    def test_a_valid_summary_still_expires_by_its_ttl(self) -> None:
        summary = self._summary(state_value=ResultState.VALID)
        self.assertEqual(summary.state(NOW + timedelta(seconds=2)), ResultState.STALE)


class CanonicalAdapterTests(unittest.TestCase):
    def test_it_preserves_the_source_and_makes_only_real_tracks(self) -> None:
        frame = _frame()
        result = DetectionResult(
            "detection_result.v1", frame, "m", "v", NOW,
            (Detection("person", 0.9, BoundingBox(1, 1, 2, 2), "t"), Detection("car", 0.8, BoundingBox(4, 4, 2, 2))),
        )
        summary = canonical_from_detection_result(result, ttl=TTL)
        self.assertEqual(len(summary.events), 2)
        self.assertEqual(len(summary.tracks), 1)
        self.assertEqual(summary.events[0].source_frame, frame)
        self.assertEqual(summary.state(NOW + timedelta(seconds=2)), ResultState.STALE)

    def test_an_untracked_detection_never_creates_a_track(self) -> None:
        result = DetectionResult(
            "detection_result.v1", _frame(), "m", "v", NOW, (Detection("person", 0.9, BoundingBox(1, 1, 2, 2)),)
        )
        summary = canonical_from_detection_result(result, ttl=TTL)
        self.assertEqual(len(summary.events), 1)
        self.assertEqual(summary.tracks, ())

    def test_it_rejects_a_non_positive_ttl(self) -> None:
        result = DetectionResult("detection_result.v1", _frame(), "m", "v", NOW, ())
        with self.assertRaises(VisionContractError):
            canonical_from_detection_result(result, ttl=timedelta(0))

    def test_it_rejects_a_non_detection_result(self) -> None:
        with self.assertRaises(VisionContractError):
            canonical_from_detection_result(object(), ttl=TTL)


if __name__ == "__main__":
    unittest.main()
