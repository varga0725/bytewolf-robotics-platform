"""P0 evidence policy and local evidence-directory contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.evidence import (
    DEFAULT_EVIDENCE_POLICY_PATH,
    EvidenceClipPlanner,
    EvidenceEvent,
    EvidencePolicyError,
    EvidenceRecord,
    FrameReference,
    LocalEvidenceDirectory,
    load_evidence_policy,
)


class _RecordingEncryptedWriter:
    """Test double: encryption is deliberately implemented outside the domain."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, bytes]] = []

    def write_encrypted(self, target: Path, payload: bytes) -> None:
        self.calls.append((target, payload))
        target.write_bytes(b"encrypted:" + payload)


class VisionEvidencePolicyTests(unittest.TestCase):
    def test_default_policy_is_metadata_only_with_short_event_clips(self) -> None:
        policy = load_evidence_policy()

        self.assertEqual(DEFAULT_EVIDENCE_POLICY_PATH.name, "evidence.v1.yaml")
        self.assertEqual(policy.default_mode, "metadata_only")
        self.assertTrue(policy.evidence_clip_enabled)
        self.assertEqual(policy.pre_event_seconds, 5)
        self.assertEqual(policy.post_event_seconds, 10)
        self.assertEqual(policy.retention_days, 7)
        self.assertFalse(policy.full_session_recording_enabled)

    def test_rejects_a_policy_that_enables_full_recording_by_default(self) -> None:
        with TemporaryDirectory() as directory:
            policy_file = Path(directory) / "bad.yaml"
            policy_file.write_text(
                """version: v1\nrecording:\n  default_mode: full_session\n  evidence_clip:\n    enabled: true\n    pre_event_seconds: 5\n    post_event_seconds: 10\n    retention_days: 7\n  full_session_recording:\n    enabled: true\n""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(EvidencePolicyError, "metadata_only"):
                load_evidence_policy(policy_file)


class VisionEvidenceClipPlannerTests(unittest.TestCase):
    def test_event_clip_plan_selects_ring_buffer_frames_and_immutable_metadata(self) -> None:
        occurred_at = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
        metadata = {"track_id": "person-7"}
        event = EvidenceEvent(
            event_id="event-1",
            occurred_at=occurred_at,
            stream_session_id="session-1",
            metadata=metadata,
        )
        metadata["track_id"] = "changed-outside"
        frames = tuple(
            FrameReference(
                frame_sequence=100 + index,
                captured_at=occurred_at + timedelta(seconds=index),
                stream_session_id="session-1",
            )
            for index in range(-8, 14)
        )

        plan = EvidenceClipPlanner(load_evidence_policy()).plan(event, frames)

        self.assertEqual(plan.start_at, occurred_at - timedelta(seconds=5))
        self.assertEqual(plan.end_at, occurred_at + timedelta(seconds=10))
        self.assertEqual(plan.frame_sequences, tuple(range(95, 111)))
        self.assertEqual(plan.retention_deadline, occurred_at + timedelta(days=7))
        self.assertEqual(plan.event.metadata, {"track_id": "person-7"})
        with self.assertRaises(TypeError):
            plan.event.metadata["new"] = "value"  # type: ignore[index]

    def test_event_clip_plan_requires_matching_session_and_aware_timestamps(self) -> None:
        occurred_at = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
        event = EvidenceEvent("event-1", occurred_at, "session-1", {})
        wrong_session = FrameReference(1, occurred_at, stream_session_id="session-2")

        with self.assertRaisesRegex(ValueError, "stream session"):
            EvidenceClipPlanner(load_evidence_policy()).plan(event, (wrong_session,))


class LocalEvidenceDirectoryTests(unittest.TestCase):
    def test_writes_only_through_injected_encrypted_writer_and_enforces_retention(self) -> None:
        now = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "evidence"
            writer = _RecordingEncryptedWriter()
            store = LocalEvidenceDirectory(root, writer)
            record = store.write_clip("event-1", b"clip", now, now + timedelta(days=7))

            self.assertEqual(writer.calls, [(root / "event-1.evidence", b"clip")])
            self.assertTrue(record.path.is_file())
            self.assertEqual(store.enforce_retention((record,), now + timedelta(days=6)), ())
            self.assertEqual(store.enforce_retention((record,), now + timedelta(days=7)), ("event-1",))
            self.assertFalse(record.path.exists())

    def test_rejects_path_traversal_and_never_deletes_outside_evidence_root(self) -> None:
        now = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "evidence"
            outside = base / "outside.evidence"
            outside.write_bytes(b"keep")
            store = LocalEvidenceDirectory(root, _RecordingEncryptedWriter())

            with self.assertRaisesRegex(ValueError, "event_id"):
                store.write_clip("../outside", b"clip", now, now + timedelta(days=7))

            expired = EvidenceRecord("event-2", outside, now, now)
            with self.assertRaisesRegex(ValueError, "outside"):
                store.enforce_retention((expired,), now)
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
