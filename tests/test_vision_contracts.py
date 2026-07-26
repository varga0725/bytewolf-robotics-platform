"""Fail-closed Vision Core contract coverage."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.contracts import (
    CAMERA_FRAME_V1,
    DETECTION_RESULT_V1,
    BoundingBox,
    CameraFrame,
    Detection,
    DetectionResult,
    FrameValidation,
    FrameSequenceLedger,
    ResultState,
    VisionContractError,
    VisionHealth,
)
from brain.vision.profiles import CAMERA_PROFILES, CameraProfileId


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def frame(**overrides: object) -> CameraFrame:
    document: dict[str, object] = {
        "contract_version": CAMERA_FRAME_V1,
        "device_id": "x500v2-01",
        "camera_id": "front-rgb",
        "stream_session_id": "session-001",
        "frame_sequence": 7,
        "captured_at": NOW - timedelta(milliseconds=20),
        "received_at": NOW - timedelta(milliseconds=5),
        "calibration_version": "front-rgb-cal-v1",
        "payload_hash": HASH,
        "encoding": "jpeg",
        "width_px": 1280,
        "height_px": 720,
        "latency_ms": 15.0,
        "dropped_frames": 2,
    }
    return CameraFrame(**{**document, **overrides})  # type: ignore[arg-type]


class CameraFrameContractTests(unittest.TestCase):
    def test_camera_frame_is_versioned_immutable_and_fresh_when_validated(self) -> None:
        sample = frame()
        result, ledger = FrameSequenceLedger().validate(sample, now=NOW)

        self.assertEqual(result.state, ResultState.VALID)
        self.assertEqual(result.frame, sample)
        self.assertEqual(ledger.last_sequence(sample), 7)
        with self.assertRaises(FrozenInstanceError):
            sample.camera_id = "other"  # type: ignore[misc]

    def test_invalid_frame_fields_fail_closed_instead_of_becoming_no_detection(self) -> None:
        cases = {
            "bad hash": frame(payload_hash="not-a-sha256"),
            "naive timestamp": frame(captured_at=NOW.replace(tzinfo=None)),
            "negative sequence": frame(frame_sequence=-1),
            "received before captured": frame(received_at=NOW - timedelta(seconds=1)),
            "unsupported encoding": frame(encoding="raw"),
        }
        for name, sample in cases.items():
            with self.subTest(name=name):
                result, _ = FrameSequenceLedger().validate(sample, now=NOW)
                self.assertEqual(result.state, ResultState.INVALID)
                self.assertTrue(result.reason)

    def test_old_but_well_formed_frame_is_explicitly_stale(self) -> None:
        sample = frame(captured_at=NOW - timedelta(seconds=2), received_at=NOW - timedelta(seconds=1.9))

        result, _ = FrameSequenceLedger().validate(sample, now=NOW, max_frame_age=timedelta(seconds=1))

        self.assertEqual(result.state, ResultState.STALE)

    def test_absent_frame_is_explicitly_missing(self) -> None:
        missing = FrameValidation.missing("camera disconnected")
        self.assertEqual(missing.state, ResultState.MISSING)
        self.assertFalse(missing.usable)

    def test_sequence_regression_and_replay_are_refused_but_new_session_can_restart(self) -> None:
        first = frame(frame_sequence=4)
        first_result, ledger = FrameSequenceLedger().validate(first, now=NOW)
        replay_result, ledger = ledger.validate(first, now=NOW)
        regression_result, ledger = ledger.validate(frame(frame_sequence=3, payload_hash="b" * 64), now=NOW)
        restarted_result, _ = ledger.validate(
            frame(stream_session_id="session-002", frame_sequence=0, payload_hash="c" * 64), now=NOW
        )

        self.assertEqual(first_result.state, ResultState.VALID)
        self.assertEqual(replay_result.state, ResultState.INVALID)
        self.assertIn("replay", replay_result.reason)
        self.assertEqual(regression_result.state, ResultState.INVALID)
        self.assertIn("sequence", regression_result.reason)
        self.assertEqual(restarted_result.state, ResultState.VALID)

    def test_clock_skew_is_refused(self) -> None:
        result, _ = FrameSequenceLedger().validate(
            frame(captured_at=NOW + timedelta(seconds=3), received_at=NOW + timedelta(seconds=3.1)),
            now=NOW,
            max_clock_skew=timedelta(seconds=2),
        )

        self.assertEqual(result.state, ResultState.INVALID)
        self.assertIn("future", result.reason)

    def test_sequence_ledger_bounds_session_memory(self) -> None:
        ledger = FrameSequenceLedger(maximum_entries=2)
        for index in range(3):
            result, ledger = ledger.validate(
                frame(stream_session_id=f"session-{index}", frame_sequence=0, payload_hash=f"{index:x}" * 64),
                now=NOW,
            )
            self.assertEqual(result.state, ResultState.VALID)
        self.assertEqual(len(ledger.entries), 2)


class DetectionContractTests(unittest.TestCase):
    def test_detection_result_carries_source_frame_and_validated_boxes(self) -> None:
        result = DetectionResult(
            contract_version=DETECTION_RESULT_V1,
            frame=frame(),
            model_id="yolo-person",
            model_version="research-2026.07",
            produced_at=NOW,
            detections=(Detection("person", 0.91, BoundingBox(10, 20, 100, 200), "track-9"),),
        )

        self.assertEqual(result.state(NOW), ResultState.VALID)
        self.assertEqual(result.detections[0].tracker_id, "track-9")

    def test_invalid_detection_data_is_refused_at_the_contract_boundary(self) -> None:
        with self.assertRaises(VisionContractError):
            DetectionResult(
                contract_version=DETECTION_RESULT_V1,
                frame=frame(),
                model_id="detector",
                model_version="v1",
                produced_at=NOW,
                detections=(Detection("person", 1.1, BoundingBox(1200, 0, 200, 10), "track-1"),),
            )

    def test_current_result_from_an_old_source_frame_is_stale(self) -> None:
        result = DetectionResult(
            contract_version=DETECTION_RESULT_V1,
            frame=frame(captured_at=NOW - timedelta(seconds=3), received_at=NOW - timedelta(seconds=2)),
            model_id="detector",
            model_version="v1",
            produced_at=NOW,
            detections=(),
        )
        self.assertEqual(result.state(NOW), ResultState.STALE)


class HealthAndProfileTests(unittest.TestCase):
    def test_health_is_derived_fail_closed_when_a_required_component_is_unavailable(self) -> None:
        health = VisionHealth(
            observed_at=NOW,
            stream_state="healthy",
            model_state="unavailable",
            gpu_state="healthy",
            backlog_frames=0,
            dropped_frames=0,
        )

        self.assertEqual(health.state(NOW), ResultState.INVALID)

    def test_health_rejects_unknown_contract_versions(self) -> None:
        with self.assertRaises(VisionContractError):
            VisionHealth(
                observed_at=NOW,
                stream_state="healthy",
                model_state="healthy",
                gpu_state="healthy",
                backlog_frames=0,
                dropped_frames=0,
                contract_version="vision_health.v2",
            )

    def test_all_declared_camera_profiles_are_explicit_registry_entries(self) -> None:
        expected = {
            "front_detection", "front_tracking", "front_biometrics", "front_gesture",
            "down_precision_landing", "down_aruco", "depth_obstacle", "recorded_benchmark", "gazebo_simulation",
        }
        self.assertEqual({profile.value for profile in CameraProfileId}, expected)
        self.assertEqual(set(CAMERA_PROFILES), set(CameraProfileId))
        self.assertTrue(CAMERA_PROFILES[CameraProfileId.DOWN_ARUCO].camera_id)
