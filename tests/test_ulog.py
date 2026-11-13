"""PX4 ULog archival coverage."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from brain.telemetry.ulog import (
    ULogCaptureError,
    archive_px4_ulog,
    discover_newest_px4_ulog,
    write_ulog_unavailable_manifest,
)


class ULogArchiveTests(unittest.TestCase):
    def test_archives_a_completed_px4_log_with_a_run_linked_integrity_manifest(self) -> None:
        raw_log = b"ULog\x01test-payload"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "px4.ulg"
            source.write_bytes(raw_log)
            capture = archive_px4_ulog(source, root / "artifacts", "run-123")
            manifest = json.loads((root / "artifacts" / "px4-ulogs" / "run-123.manifest.json").read_text())

        self.assertEqual(capture.relative_path, "px4-ulogs/run-123.ulg")
        self.assertEqual(manifest["run_id"], "run-123")
        self.assertEqual(manifest["sha256"], sha256(raw_log).hexdigest())
        self.assertEqual(manifest["size_bytes"], len(raw_log))
        self.assertEqual(manifest["status"], "captured")

    def test_records_an_explicit_unavailable_status_instead_of_claiming_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = write_ulog_unavailable_manifest(Path(directory), "run-123", "SITL logger disabled")
            manifest = json.loads(manifest_path.read_text())

        self.assertEqual(manifest["status"], "unavailable")
        self.assertEqual(manifest["reason"], "SITL logger disabled")

    def test_discovers_the_newest_ulg_under_a_px4_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            older = run_root / "log" / "2026-01-01" / "older.ulg"
            newer = run_root / "log" / "2026-01-02" / "newer.ulg"
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            newer.touch()
            older.touch()
            import os
            import time

            now = time.time()
            os.utime(older, (now - 10, now - 10))
            os.utime(newer, (now, now))

            discovered = discover_newest_px4_ulog(run_root)

        self.assertEqual(discovered, newer)

    def test_refuses_missing_or_non_px4_log_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ULogCaptureError, ".ulg"):
                archive_px4_ulog(root / "not-a-log.txt", root / "artifacts", "run-123")
            with self.assertRaisesRegex(ULogCaptureError, "not a readable file"):
                archive_px4_ulog(root / "missing.ulg", root / "artifacts", "run-123")


if __name__ == "__main__":
    unittest.main()
