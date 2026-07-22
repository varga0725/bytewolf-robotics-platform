"""Bounded, explicit event evidence-capture tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, CameraFrame
from brain.vision.evidence import (
    EvidenceCaptureBuffer,
    EvidenceCaptureError,
    EvidenceEvent,
    LocalEvidenceDirectory,
    load_evidence_policy,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


class RecordingWriter:
    def write_encrypted(self, target: Path, payload: bytes) -> None:
        target.write_bytes(payload)


def frame(sequence: int, captured_at: datetime, payload: bytes) -> CameraFrame:
    return CameraFrame(
        CAMERA_FRAME_V1, "gazebo-x500-01", "front-rgb", "session-a", sequence,
        captured_at, captured_at, "cal-v1", hashlib.sha256(payload).hexdigest(),
        "jpeg", 2, 2, 0.0, 0,
    )


class EvidenceCaptureBufferTests(unittest.TestCase):
    def test_explicit_event_writes_only_the_bounded_pre_and_post_window(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalEvidenceDirectory(Path(temporary) / "evidence", RecordingWriter())
            buffer = EvidenceCaptureBuffer(load_evidence_policy(), store, max_frames=20, max_payload_bytes=1024)
            for sequence, seconds in enumerate((-6, -5, 0, 10, 11)):
                payload = f"frame-{sequence}".encode()
                buffer.record(frame(sequence, NOW + timedelta(seconds=seconds), payload), payload)
            event = EvidenceEvent("event-1", NOW, "session-a", {"kind": "person-observation"})
            buffer.request(event)

            captured = buffer.finalize("event-1", finalized_at=NOW + timedelta(seconds=10))

            self.assertEqual(captured.frame_sequences, (1, 2, 3))
            envelope = json.loads(captured.record.path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["contract_version"], "vision_evidence_clip.v1")
            self.assertEqual([entry["frame_sequence"] for entry in envelope["frames"]], [1, 2, 3])
            self.assertEqual(base64.b64decode(envelope["frames"][0]["payload_base64"]), b"frame-1")
            self.assertNotIn("frame-0", captured.record.path.read_text(encoding="utf-8"))
            self.assertEqual(buffer.enforce_retention(NOW + timedelta(days=7, seconds=10)), ("event-1",))
            self.assertFalse(captured.record.path.exists())

    def test_does_not_write_before_the_configured_post_event_window_ends(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalEvidenceDirectory(Path(temporary) / "evidence", RecordingWriter())
            buffer = EvidenceCaptureBuffer(load_evidence_policy(), store, max_frames=20, max_payload_bytes=1024)
            payload = b"frame"
            buffer.record(frame(1, NOW, payload), payload)
            buffer.request(EvidenceEvent("event-1", NOW, "session-a", {}))

            with self.assertRaisesRegex(EvidenceCaptureError, "post-event"):
                buffer.finalize("event-1", finalized_at=NOW + timedelta(seconds=9))

    def test_requires_captured_evidence_through_the_post_event_boundary(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalEvidenceDirectory(Path(temporary) / "evidence", RecordingWriter())
            buffer = EvidenceCaptureBuffer(load_evidence_policy(), store, max_frames=20, max_payload_bytes=1024)
            payload = b"frame"
            buffer.record(frame(1, NOW, payload), payload)
            buffer.request(EvidenceEvent("event-1", NOW, "session-a", {}))

            with self.assertRaisesRegex(EvidenceCaptureError, "complete post-event"):
                buffer.finalize("event-1", finalized_at=NOW + timedelta(seconds=10))

    def test_rejects_unverified_payload_and_does_not_persist_without_explicit_event(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LocalEvidenceDirectory(root / "evidence", RecordingWriter())
            buffer = EvidenceCaptureBuffer(load_evidence_policy(), store, max_frames=20, max_payload_bytes=1024)
            payload = b"frame"

            with self.assertRaisesRegex(EvidenceCaptureError, "hash"):
                buffer.record(frame(1, NOW, payload), b"different")
            self.assertFalse((root / "evidence").exists())

    def test_rejects_event_for_an_unseen_or_mismatched_stream_session(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalEvidenceDirectory(Path(temporary) / "evidence", RecordingWriter())
            buffer = EvidenceCaptureBuffer(load_evidence_policy(), store, max_frames=20, max_payload_bytes=1024)
            payload = b"frame"
            buffer.record(frame(1, NOW, payload), payload)

            with self.assertRaisesRegex(EvidenceCaptureError, "stream session"):
                buffer.request(EvidenceEvent("event-1", NOW, "session-b", {}))


if __name__ == "__main__":
    unittest.main()
