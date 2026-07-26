"""The read-only Vision views the Control Room reads.

Two properties are pinned here, and they guard different things. The allowlist
keeps the producer's local artifact directory from leaking raw payloads,
embeddings, templates or evidence locations into a view that only needs counts
and states. The ageing is why the state is recomputed on every read rather than
relayed: the producer writes this file and stops, so nothing rewrites it when
the producer dies, and a stalled run's last `valid` would otherwise be served
as live perception indefinitely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from apps.api.server import VISION_STATUS_MAX_AGE_S, _vision_read_model, create_app


def _status_document(observed_at: datetime | None = None) -> dict[str, object]:
    # Freshly observed by default: the endpoint ages a published status on every
    # read, so a fixed historical timestamp would be served as stale.
    moment = observed_at or datetime.now(UTC)
    return {
        "contract_version": "vision_dashboard.v1",
        "state": "valid",
        "observed_at": moment.isoformat().replace("+00:00", "Z"),
        "track_count": 1,
        "detections": [],
        "backlog_frames": 0,
        "dropped_frames": 0,
        "stream_state": "healthy",
        "model_state": "healthy",
        "gpu_state": "healthy",
    }


class VisionEndpointTests(unittest.TestCase):
    def test_it_serves_the_status_and_the_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")
            status = root / "vision-status.json"
            document = _status_document()
            status.write_text(json.dumps(document), encoding="utf-8")
            frame = root / "frame.jpg"
            frame.write_bytes(b"\xff\xd8frame-bytes")

            client = TestClient(
                create_app(telemetry, vision_status_path=status, vision_frame_path=frame)
            )

            self.assertEqual(client.get("/api/v1/vision").json(), document)
            frame_response = client.get("/api/v1/vision/frame")
            self.assertEqual(frame_response.status_code, 200)
            self.assertEqual(frame_response.content, b"\xff\xd8frame-bytes")

    def test_unconfigured_artifacts_are_absent_rather_than_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry = Path(directory) / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")

            client = TestClient(create_app(telemetry))

            for endpoint in ("/api/v1/vision", "/api/v1/vision/frame"):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(client.get(endpoint).status_code, 404)

    def test_a_malformed_status_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")
            status = root / "vision-status.json"
            status.write_text("{ not json", encoding="utf-8")

            client = TestClient(create_app(telemetry, vision_status_path=status))

            self.assertEqual(client.get("/api/v1/vision").status_code, 400)

    def test_a_status_carrying_more_than_the_contract_is_refused(self) -> None:
        # The producer's directory is a boundary: a field nobody allowed must
        # not reach the browser merely because it happened to be in the file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")
            status = root / "vision-status.json"
            document = _status_document() | {"embedding_path": "/var/vision/templates/a.bin"}
            status.write_text(json.dumps(document), encoding="utf-8")

            client = TestClient(create_app(telemetry, vision_status_path=status))

            self.assertEqual(client.get("/api/v1/vision").status_code, 400)


class DetectionContractTests(unittest.TestCase):
    """Every detection in the view is checked, not just the document around it.

    The allowlist above governs which top-level fields may be relayed; this
    governs what may sit inside `detections`. A confidence the producer could
    not compute, or a box that is not a box, must not reach an overlay that
    draws it on a camera frame as though it were a measurement.
    """

    def _model(self, detection: object):
        return _vision_read_model(_status_document() | {"detections": [detection]})

    @staticmethod
    def _detection(**overrides) -> dict:
        detection = {
            "label": "person",
            "confidence": 0.9,
            "tracker_id": "local-000001",
            "bounding_box": {"x_px": 0, "y_px": 0, "width_px": 1, "height_px": 1},
        }
        return detection | overrides

    def test_a_well_formed_detection_is_relayed(self) -> None:
        self.assertEqual(len(self._model(self._detection())["detections"]), 1)

    def test_a_confidence_that_is_not_a_probability_is_refused(self) -> None:
        for confidence in (float("nan"), float("inf"), -0.1, 1.1, "0.9", None):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                self._model(self._detection(confidence=confidence))

    def test_a_box_that_is_not_a_box_is_refused(self) -> None:
        for box in (
            {"x_px": -1, "y_px": 0, "width_px": 1, "height_px": 1},   # off-frame origin
            {"x_px": 0, "y_px": 0, "width_px": 0, "height_px": 1},    # zero width
            {"x_px": 0, "y_px": 0, "width_px": 1.5, "height_px": 1},  # not pixels
            {"x_px": 0, "y_px": 0, "width_px": 1},                    # missing a side
            "0,0,1,1",
        ):
            with self.subTest(box=box), self.assertRaises(ValueError):
                self._model(self._detection(bounding_box=box))

    def test_a_detection_carrying_more_than_the_contract_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._model(self._detection(embedding=[0.1, 0.2]))

    def test_a_tracker_id_may_be_absent_but_not_a_number(self) -> None:
        self.assertEqual(len(self._model(self._detection(tracker_id=None))["detections"]), 1)
        with self.assertRaises(ValueError):
            self._model(self._detection(tracker_id=7))


class VisionFreshnessTests(unittest.TestCase):
    """A stalled producer leaves a valid file behind; it must not read as live."""

    NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

    def _model(self, observed_at: datetime, **overrides):
        document = _status_document(observed_at) | overrides
        return _vision_read_model(document, now=self.NOW)

    def test_a_recent_observation_stays_valid(self) -> None:
        model = self._model(self.NOW - timedelta(seconds=VISION_STATUS_MAX_AGE_S / 2))
        self.assertEqual(model["state"], "valid")

    def test_an_aged_out_observation_is_served_as_stale(self) -> None:
        model = self._model(self.NOW - timedelta(seconds=VISION_STATUS_MAX_AGE_S + 1))
        self.assertEqual(model["state"], "stale")

    def test_a_future_timestamp_is_not_treated_as_fresh(self) -> None:
        # A clock that ran backwards is a broken clock, not perpetual freshness.
        self.assertEqual(self._model(self.NOW + timedelta(hours=1))["state"], "stale")

    def test_a_state_the_producer_already_refused_is_left_alone(self) -> None:
        model = self._model(self.NOW - timedelta(hours=1), state="invalid")
        self.assertEqual(model["state"], "invalid")


if __name__ == "__main__":
    unittest.main()
