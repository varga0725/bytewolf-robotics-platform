"""A recorded clip replays through the existing fixture ingest, byte for byte.

This is what makes a camera run measurable: record once, replay deterministically,
and vary the detector or tracker under test against a fixed input. The round trip
is the guarantee -- every frame the recorder writes must come back out of
RecordedJsonlIngest with the same identity and the same payload bytes.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

import numpy as np

from brain.cli.vision_uvc_recorder import record_clip
from brain.vision.contracts import BoundingBox, Detection
from brain.vision.recorded import RecordedJsonlIngest
from brain.vision.uvc import UvcCameraSource, UvcCaptureError


class _FakeCapture:
    def __init__(self, count=3, fail_every=0):
        self._remaining = count
        self._fail_every = fail_every
        self._reads = 0

    def isOpened(self):  # noqa: N802 - OpenCV's name
        return True

    def read(self):
        self._reads += 1
        if self._fail_every and self._reads % self._fail_every == 0:
            return False, None
        if self._remaining <= 0:
            return False, None
        self._remaining -= 1
        # A distinct image per frame, so identical payloads cannot hide a bug.
        image = np.zeros((32, 40, 3), dtype=np.uint8)
        image[:, : self._remaining + 1] = 255
        return True, image

    def get(self, _prop):
        return 30.0

    def release(self):
        pass


class _StubDetector:
    def __init__(self, resolver):
        self._resolver = resolver

    def detect(self, frame, _produced_at):
        self._resolver.resolve(frame.payload_hash)
        return (Detection("person", 0.8, BoundingBox(1, 1, 8, 8), "t-1"),)


def _source(capture):
    moments = iter(
        datetime(2026, 7, 25, 10, 0, tzinfo=UTC) + timedelta(milliseconds=100 * i) for i in range(1000)
    )
    return UvcCameraSource(
        0, device_id="hawkeye-usb", camera_id="front_rgb",
        capture_factory=lambda _index: capture, now=lambda: next(moments),
    )


class RecorderRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clip = Path(self._tmp.name) / "clips" / "walk.jsonl"

    def _record(self, capture=None, **kwargs):
        source = _source(capture or _FakeCapture())
        source.open()
        try:
            return source, record_clip(source, self.clip, frames=3, **kwargs)
        finally:
            source.close()

    def test_every_recorded_frame_replays_with_its_payload(self) -> None:
        source, summary = self._record()
        self.assertEqual(summary["frames"], 3)
        self.assertTrue(self.clip.exists())

        ingest = RecordedJsonlIngest(self.clip)
        replayed = []
        while True:
            frame = ingest.poll()
            if frame is None:
                break
            payload = ingest.payload_for(frame)
            # The fixture ingest itself verifies the hash; assert identity too.
            self.assertEqual(frame.camera_id, "front_rgb")
            self.assertEqual(frame.encoding, "jpeg")
            replayed.append((frame.frame_sequence, frame.payload_hash, len(payload)))

        self.assertEqual([sequence for sequence, _hash, _size in replayed], [1, 2, 3])
        # Distinct frames must have distinct payloads: a recorder that wrote the
        # same bytes for every frame would still round-trip, and be useless.
        self.assertEqual(len({hash_ for _seq, hash_, _size in replayed}), 3)

    def test_the_summary_reports_a_reproducible_digest(self) -> None:
        _source_, summary = self._record()
        self.assertEqual(len(summary["sha256"]), 64)
        self.assertGreater(summary["bytes"], 0)
        self.assertEqual(summary["dropped"], 0)

    def test_recorded_detections_replay_when_asked_for(self) -> None:
        source = _source(_FakeCapture())
        source.open()
        try:
            record_clip(source, self.clip, frames=2, detector=_StubDetector(source))
        finally:
            source.close()

        ingest = RecordedJsonlIngest(self.clip)
        frame = ingest.poll()
        detections = ingest.detections_for(frame)
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "person")
        self.assertEqual(detections[0].tracker_id, "t-1")

    def test_without_a_detector_the_clip_carries_no_detections(self) -> None:
        # The clip is the fixed input; the model under test is varied at replay.
        self._record()
        ingest = RecordedJsonlIngest(self.clip)
        frame = ingest.poll()
        self.assertEqual(ingest.detections_for(frame), ())

    def test_a_dropped_read_does_not_end_the_recording(self) -> None:
        _source_, summary = self._record(capture=_FakeCapture(count=3, fail_every=3))
        self.assertEqual(summary["frames"], 3)
        self.assertGreaterEqual(summary["dropped"], 1)


class SafetyTests(unittest.TestCase):
    def test_the_recorder_never_imports_flight_control(self) -> None:
        source = Path(__file__).resolve().parents[1] / "brain/cli/vision_uvc_recorder.py"
        text = source.read_text(encoding="utf-8").lower()
        for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
            with self.subTest(needle=needle):
                self.assertNotIn(f"import {needle}", text)
                self.assertNotIn(f"from {needle}", text)


if __name__ == "__main__":
    unittest.main()
