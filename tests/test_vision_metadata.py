"""P0 local metadata-store contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.metadata import LocalVisionMetadataStore, VisionMetadataError


_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _status() -> dict[str, object]:
    return {
        "contract_version": "vision_dashboard.v1", "state": "valid",
        "observed_at": "2026-07-22T12:00:00Z", "track_count": 1,
        "detections": [{"label": "person", "confidence": 0.9, "tracker_id": "local-000001",
                        "bounding_box": {"x_px": 1, "y_px": 2, "width_px": 3, "height_px": 4}}],
        "backlog_frames": 0, "dropped_frames": 2, "stream_state": "healthy",
        "model_state": "healthy", "gpu_state": "degraded",
    }


class LocalVisionMetadataStoreTests(unittest.TestCase):
    def test_appends_versioned_read_only_dashboard_metadata(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "metadata.jsonl"
            store = LocalVisionMetadataStore(path)

            record = store.append_dashboard_status(_status(), written_at=_NOW)

            self.assertEqual(record.contract_version, "vision_metadata.v1")
            self.assertEqual(record.sequence, 0)
            line = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(line["sequence"], 0)
            self.assertEqual(line["written_at"], "2026-07-22T12:00:00Z")
            self.assertEqual(line["status"]["detections"][0]["tracker_id"], "local-000001")

    def test_reopens_existing_journal_and_monotonically_advances_sequence(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "metadata.jsonl"
            LocalVisionMetadataStore(path).append_dashboard_status(_status(), written_at=_NOW)

            record = LocalVisionMetadataStore(path).append_dashboard_status(_status(), written_at=_NOW)

            self.assertEqual(record.sequence, 1)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_rejects_sensitive_and_non_dashboard_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalVisionMetadataStore(Path(temporary) / "metadata.jsonl")
            status = _status()
            status["payload"] = "raw-image-bytes"

            with self.assertRaisesRegex(VisionMetadataError, "read-only dashboard"):
                store.append_dashboard_status(status, written_at=_NOW)

    def test_rejects_sensitive_nested_detection_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            store = LocalVisionMetadataStore(Path(temporary) / "metadata.jsonl")
            status = _status()
            status["detections"][0]["payload"] = "raw-image-bytes"  # type: ignore[index]

            with self.assertRaisesRegex(VisionMetadataError, "detections"):
                store.append_dashboard_status(status, written_at=_NOW)

    def test_refuses_a_malformed_existing_journal(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "metadata.jsonl"
            LocalVisionMetadataStore(path).append_dashboard_status(_status(), written_at=_NOW)
            with path.open("a", encoding="utf-8") as journal:
                journal.write("not-json\n")

            with self.assertRaisesRegex(VisionMetadataError, "line 2"):
                LocalVisionMetadataStore(path)


if __name__ == "__main__":
    unittest.main()
