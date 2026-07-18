"""Projecting a down-camera detection to a ground target, failing closed.

The geometry is unit-checked here; a down-camera SITL scenario with a marker at
a known world position confirms the north/east sign against ground truth. Every
way the projection could invent a position it has no right to -- unknown
altitude, too much tilt, an untrusted detection -- must resolve to a state a
consumer cannot act on.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.perception.detector import BoundingBox, Detection, DetectionResult
from brain.perception.target_estimator import (
    GlobalFix,
    GroundTargetEstimator,
    TargetEstimationError,
    TargetState,
    validate_target_document,
)


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)


def _result(centre_u: float, centre_v: float, *, validity: str = "valid", confidence: float = 0.95, detections=None) -> DetectionResult:
    if detections is None:
        detections = (Detection("landing-pad", confidence, BoundingBox(centre_u - 20, centre_v - 20, 40, 40)),)
    return DetectionResult(
        captured_at=_NOW, max_age_s=0.5, declared_validity=validity,
        frame_width=1280, frame_height=960, frame_id="f1", detections=tuple(detections), source="down",
    )


def _est(**kwargs) -> GroundTargetEstimator:
    return GroundTargetEstimator(source="gz mono_cam_down + stub", **kwargs)


class GeometryTests(unittest.TestCase):
    def test_a_centred_target_is_straight_below_at_slant_range_altitude(self) -> None:
        result = _est().estimate(_result(640, 480), altitude_agl_m=10.0, now=_NOW)

        self.assertEqual(result.state(_NOW), TargetState.VALID)
        self.assertAlmostEqual(result.offset_north_m, 0.0, places=6)
        self.assertAlmostEqual(result.offset_east_m, 0.0, places=6)
        self.assertAlmostEqual(result.range_m, 10.0, places=3)

    def test_image_axes_map_to_the_world_as_confirmed_by_ground_truth(self) -> None:
        """At yaw 0 the body forward axis points world east (gz ENU); the down-camera
        SITL scenario pinned this mapping after an earlier one came out rotated 90 deg."""
        above = _est().estimate(_result(640, 210), altitude_agl_m=10.0, now=_NOW)  # image up
        left = _est().estimate(_result(370, 480), altitude_agl_m=10.0, now=_NOW)   # image left

        # Image up is body forward, which at yaw 0 is world east.
        self.assertGreater(above.offset_east_m, 4.0)
        self.assertAlmostEqual(above.offset_north_m, 0.0, places=6)
        # Image left is body left, which at yaw 0 is world north.
        self.assertGreater(left.offset_north_m, 4.0)
        self.assertAlmostEqual(left.offset_east_m, 0.0, places=6)

    def test_the_ground_offset_scales_with_altitude(self) -> None:
        low = _est().estimate(_result(640, 210), altitude_agl_m=10.0, now=_NOW)
        high = _est().estimate(_result(640, 210), altitude_agl_m=20.0, now=_NOW)

        self.assertAlmostEqual(high.offset_east_m, 2 * low.offset_east_m, places=6)

    def test_uncertainty_grows_with_altitude(self) -> None:
        low = _est().estimate(_result(640, 480), altitude_agl_m=10.0, now=_NOW)
        high = _est().estimate(_result(640, 480), altitude_agl_m=30.0, now=_NOW)

        self.assertGreater(high.horizontal_uncertainty_m, low.horizontal_uncertainty_m)

    def test_yaw_turns_the_body_forward_axis_toward_north(self) -> None:
        """At yaw 90 the body forward axis points world north, so image up reads north."""
        facing_north = _est().estimate(_result(640, 210), altitude_agl_m=10.0, now=_NOW, yaw_deg=90.0)

        self.assertGreater(facing_north.offset_north_m, 4.0)
        self.assertAlmostEqual(facing_north.offset_east_m, 0.0, places=6)


class GlobalFixTests(unittest.TestCase):
    def test_a_gps_origin_yields_an_absolute_target_fix(self) -> None:
        result = _est().estimate(
            _result(640, 210), altitude_agl_m=10.0, now=_NOW, global_position=GlobalFix(47.3977, 8.5456)
        )

        self.assertIsNotNone(result.global_fix)
        self.assertNotAlmostEqual(result.global_fix.longitude_deg, 8.5456, places=6)
        self.assertAlmostEqual(result.global_fix.latitude_deg, 47.3977, places=6)

    def test_without_a_gps_origin_there_is_no_absolute_fix(self) -> None:
        result = _est().estimate(_result(910, 480), altitude_agl_m=10.0, now=_NOW)

        self.assertIsNone(result.global_fix)
        self.assertNotIn("global_position", result.to_document()["target"])


class FailClosedTests(unittest.TestCase):
    def test_an_unknown_altitude_cannot_produce_a_target(self) -> None:
        for altitude in (0.0, -5.0, float("nan"), float("inf")):
            with self.subTest(altitude=altitude):
                result = _est().estimate(_result(640, 480), altitude_agl_m=altitude, now=_NOW)
                self.assertEqual(result.state(_NOW), TargetState.INVALID)

    def test_too_much_tilt_breaks_the_flat_ground_projection(self) -> None:
        result = _est(max_tilt_deg=10.0).estimate(_result(640, 480), altitude_agl_m=10.0, now=_NOW, tilt_deg=25.0)

        self.assertEqual(result.state(_NOW), TargetState.INVALID)

    def test_an_untrusted_detection_yields_no_trustworthy_target(self) -> None:
        result = _est().estimate(_result(640, 480, validity="invalid"), altitude_agl_m=10.0, now=_NOW)

        self.assertEqual(result.state(_NOW), TargetState.INVALID)

    def test_a_stale_detection_is_not_projected_into_a_fresh_target(self) -> None:
        # The detection was fresh at capture but is read 1 s later, past its max_age.
        result = _est().estimate(_result(640, 480), altitude_agl_m=10.0, now=_NOW + timedelta(seconds=1))

        self.assertIn(result.state(_NOW + timedelta(seconds=1)), (TargetState.INVALID, TargetState.STALE))

    def test_no_detections_means_no_target_not_the_origin(self) -> None:
        result = _est().estimate(_result(0, 0, detections=()), altitude_agl_m=10.0, now=_NOW)

        self.assertEqual(result.state(_NOW), TargetState.MISSING)
        with self.assertRaisesRegex(TargetEstimationError, "missing"):
            result.usable_offset_m(_NOW)

    def test_the_most_confident_detection_is_the_one_projected(self) -> None:
        detections = (
            Detection("weed", 0.30, BoundingBox(100, 100, 20, 20)),
            Detection("landing-pad", 0.95, BoundingBox(620, 460, 40, 40)),
        )
        result = _est().estimate(_result(0, 0, detections=detections), altitude_agl_m=10.0, now=_NOW)

        self.assertEqual(result.label, "landing-pad")


class ContractTests(unittest.TestCase):
    def test_a_valid_observation_is_schema_valid(self) -> None:
        document = _est().estimate(_result(910, 480), altitude_agl_m=10.0, now=_NOW).to_document()

        validate_target_document(document)
        self.assertEqual(document["frame"], "local_ned")
        self.assertIn("range_m", document["target"])

    def test_an_invalid_observation_carries_no_target(self) -> None:
        document = _est().estimate(_result(640, 480), altitude_agl_m=-1.0, now=_NOW).to_document()

        self.assertEqual(document["validity"], "invalid")
        self.assertNotIn("target", document)
        validate_target_document(document)

    def test_the_estimator_imports_no_flight_or_mavsdk_path(self) -> None:
        import ast
        from pathlib import Path

        import brain.perception.target_estimator as estimator

        tree = ast.parse(Path(estimator.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for module in imported:
            self.assertNotIn("mavsdk", module)
            self.assertNotIn("adapters", module)


if __name__ == "__main__":
    unittest.main()
