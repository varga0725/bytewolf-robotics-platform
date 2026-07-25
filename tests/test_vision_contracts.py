"""Coverage for the shared Vision v0.1 contracts.

These are the versioned events a consumer reads instead of the Vision internals,
so most of these tests are about refusal and freshness: a future version, a box
outside its frame, a confidence out of range, a naive timestamp, and a detection
older than its max_age_s resolving to stale. A static test locks that the vision
domain never imports flight control.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from brain.vision.canonical import (
    VISION_CONTRACT_VERSION,
    VisionContractError,
    VisionState,
    load_detection_event,
    load_vision_health,
    load_vision_summary,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared/interfaces/vision/examples"
VISION_PKG = ROOT / "brain/vision"

_LOADERS = {
    "detection_event": load_detection_event,
    "vision_summary": load_vision_summary,
    "vision_health": load_vision_health,
}


def _loader_for(path: Path):
    for prefix, loader in _LOADERS.items():
        if path.name.startswith(prefix):
            return loader
    raise AssertionError(f"fixture '{path.name}' has no known contract prefix")


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self) -> None:
        fixtures = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(fixtures, "the contract needs valid fixtures to be worth anything")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                _loader_for(path)(_read(path))

    def test_every_invalid_fixture_is_rejected(self) -> None:
        fixtures = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(fixtures, "refusal is the point; there must be invalid fixtures")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                with self.assertRaises(VisionContractError):
                    _loader_for(path)(_read(path))


class FreshnessTests(unittest.TestCase):
    def _event(self, **overrides) -> dict:
        document = {
            "contract_version": VISION_CONTRACT_VERSION,
            "event_id": "e", "source": "camera:front_rgb",
            "observed_at": "2026-07-24T09:00:00+00:00", "max_age_s": 0.5, "validity": "valid",
            "frame": {"width_px": 1280, "height_px": 720},
            "model_id": "yolo", "model_version": "v8",
            "label": "person", "confidence": 0.9,
            "bounding_box": {"x_px": 1.0, "y_px": 1.0, "width_px": 2.0, "height_px": 2.0},
        }
        return {**document, **overrides}

    def test_a_fresh_detection_is_valid(self) -> None:
        event = load_detection_event(self._event())
        now = datetime(2026, 7, 24, 9, 0, 0, 200000, tzinfo=UTC)
        self.assertEqual(event.state(now), VisionState.VALID)
        self.assertTrue(event.state(now).usable)

    def test_a_detection_past_its_max_age_is_stale(self) -> None:
        event = load_detection_event(self._event())
        now = datetime(2026, 7, 24, 9, 0, 2, tzinfo=UTC)
        self.assertEqual(event.state(now), VisionState.STALE)
        self.assertFalse(event.state(now).usable)

    def test_producer_declared_invalid_and_missing_are_kept(self) -> None:
        now = datetime(2026, 7, 24, 9, 0, 0, tzinfo=UTC)
        self.assertEqual(load_detection_event(self._event(validity="invalid")).state(now), VisionState.INVALID)
        self.assertEqual(load_detection_event(self._event(validity="missing")).state(now), VisionState.MISSING)

    def test_a_naive_now_is_refused(self) -> None:
        event = load_detection_event(self._event())
        with self.assertRaises(VisionContractError):
            event.state(datetime(2026, 7, 24, 9, 0, 0))


class FailClosedFreshnessTests(unittest.TestCase):
    """A freshness window that cannot expire, or a clock that runs ahead, must
    not keep an observation actionable."""

    def _event(self, **overrides) -> dict:
        return {**_read(EXAMPLES / "valid/detection_event.v0_1.json"), **overrides}

    def test_an_infinite_max_age_is_refused(self) -> None:
        # JSON has no infinity literal, but 1e400 parses to inf and satisfies a
        # lower-bound-only schema check; an infinite window never goes stale.
        document = json.loads('{"max_age_s": 1e400}')
        self.assertEqual(document["max_age_s"], float("inf"))
        with self.assertRaises(VisionContractError):
            load_detection_event(self._event(max_age_s=document["max_age_s"]))

    def test_an_infinite_max_age_is_refused_for_summary_and_health(self) -> None:
        infinite = json.loads('{"v": 1e400}')["v"]
        with self.assertRaises(VisionContractError):
            load_vision_summary({**_read(EXAMPLES / "valid/vision_summary.v0_1.json"), "max_age_s": infinite})
        with self.assertRaises(VisionContractError):
            load_vision_health({**_read(EXAMPLES / "valid/vision_health.v0_1.json"), "max_age_s": infinite})

    def test_a_far_future_timestamp_is_invalid_not_brand_new(self) -> None:
        event = load_detection_event(self._event())
        # Clamping a negative age to zero would keep this usable until wall-clock
        # time caught up, and for max_age_s beyond that.
        an_hour_early = event.observed_at - timedelta(hours=1)
        self.assertEqual(event.state(an_hour_early), VisionState.INVALID)

    def test_small_clock_skew_is_tolerated(self) -> None:
        event = load_detection_event(self._event())
        slightly_early = event.observed_at - timedelta(seconds=1)
        self.assertEqual(event.state(slightly_early), VisionState.VALID)


class InvariantTests(unittest.TestCase):
    def test_future_version_is_rejected(self) -> None:
        with self.assertRaises(VisionContractError):
            load_detection_event({**_read(EXAMPLES / "valid/detection_event.v0_1.json"), "contract_version": "v0.2"})

    def test_a_box_outside_the_frame_is_rejected(self) -> None:
        document = _read(EXAMPLES / "valid/detection_event.v0_1.json")
        document["bounding_box"] = {"x_px": 1270.0, "y_px": 1.0, "width_px": 20.0, "height_px": 10.0}
        with self.assertRaises(VisionContractError):
            load_detection_event(document)

    def test_a_summary_detection_outside_the_frame_is_rejected(self) -> None:
        document = _read(EXAMPLES / "valid/vision_summary.v0_1.json")
        document["detections"][0]["bounding_box"] = {"x_px": 1270.0, "y_px": 1.0, "width_px": 20.0, "height_px": 10.0}
        with self.assertRaises(VisionContractError):
            load_vision_summary(document)

    def test_a_count_below_the_listed_detections_is_refused(self) -> None:
        # The count is the truth and consumers are told to prefer it, so a count
        # under the listed entries would hide objects the document carries.
        document = _read(EXAMPLES / "valid/vision_summary.v0_1.json")
        document["detection_count"] = 1
        with self.assertRaises(VisionContractError):
            load_vision_summary(document)

    def test_an_artifact_hash_must_be_a_real_sha256(self) -> None:
        document = _read(EXAMPLES / "valid/detection_event.v0_1.json")
        document["artifact_ref"]["payload_hash"] = "9f86d0818"
        with self.assertRaises(VisionContractError):
            load_detection_event(document)

    def test_detection_count_can_exceed_the_listed_detections(self) -> None:
        # The list may be capped; the count is the truth. A valid summary with a
        # larger count than listed detections must still load.
        document = _read(EXAMPLES / "valid/vision_summary.v0_1.json")
        document["detection_count"] = 9
        summary = load_vision_summary(document)
        self.assertEqual(summary.detection_count, 9)
        self.assertEqual(len(summary.detections), 2)


class SafetyBoundaryTests(unittest.TestCase):
    def test_the_vision_domain_never_imports_flight_control(self) -> None:
        for source in VISION_PKG.rglob("*.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


if __name__ == "__main__":
    unittest.main()
