"""The V1 perception loop turns a marker frame into a safety-checked move.

These drive the pure decision core with synthetic down-camera frames: a red
marker at a known pixel becomes an approved waypoint pointing at it, and every
way the chain could fail -- no marker, too much tilt, an unknown altitude, a fix
outside the geofence -- fails closed to a named refusal, never a waypoint. The
sign of the projection is confirmed against Gazebo ground truth elsewhere; here
the composition and the fail-closed seams are what is checked.
"""

from datetime import UTC, datetime, timedelta
import unittest

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.target_approach import plan_target_approach
from brain.perception.target_estimator import CameraIntrinsics, GroundTargetEstimator
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
_GREY = (128, 128, 128)
_RED = (220, 20, 20)
# A small frame keeps the pixel loop fast; the intrinsics are built to match it,
# so the geometry is identical in ratio to the 1080p down camera.
_WIDTH, _HEIGHT = 320, 240
_FOV_RAD = 1.74
_INTRINSICS = CameraIntrinsics(_WIDTH, _HEIGHT, _FOV_RAD)


def _image_with_red_square(box: tuple[int, int, int, int]) -> bytes:
    bx, by, bw, bh = box
    pixels = bytearray(_WIDTH * _HEIGHT * 3)
    for v in range(_HEIGHT):
        for u in range(_WIDTH):
            colour = _RED if bx <= u < bx + bw and by <= v < by + bh else _GREY
            index = (v * _WIDTH + u) * 3
            pixels[index], pixels[index + 1], pixels[index + 2] = colour
    return bytes(pixels)


def _marker_frame(box: tuple[int, int, int, int], *, captured_at: datetime = _NOW) -> CameraFrame:
    return CameraFrame(
        sensor_id="down_rgb", encoding=FrameEncoding.RGB8, width=_WIDTH, height=_HEIGHT,
        data=_image_with_red_square(box), captured_at=captured_at, frame_id="down-approach",
    )


def _grey_frame() -> CameraFrame:
    return CameraFrame(
        sensor_id="down_rgb", encoding=FrameEncoding.RGB8, width=_WIDTH, height=_HEIGHT,
        data=bytes(_GREY * (_WIDTH * _HEIGHT)), captured_at=_NOW, frame_id="down-empty",
    )


def _detector() -> DetectorAdapter:
    return DetectorAdapter(
        ColourMarkerBackend(ColourTarget(*_RED), min_pixels=20, sample_step=1), source="down + colour"
    )


def _estimator() -> GroundTargetEstimator:
    return GroundTargetEstimator(_INTRINSICS, source="down approach")


def _gate() -> SafetyGate:
    return SafetyGate(load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH).flight_limits())


def _plan(frame, *, altitude_agl_m=8.0, vehicle_north_m=0.0, vehicle_east_m=0.0,
          now=_NOW, approach_altitude_m=5.0, yaw_deg=0.0, tilt_deg=0.0, max_uncertainty_m=3.0):
    return plan_target_approach(
        frame, detector=_detector(), estimator=_estimator(), gate=_gate(),
        altitude_agl_m=altitude_agl_m, vehicle_north_m=vehicle_north_m, vehicle_east_m=vehicle_east_m,
        now=now, approach_altitude_m=approach_altitude_m, yaw_deg=yaw_deg, tilt_deg=tilt_deg,
        max_uncertainty_m=max_uncertainty_m,
    )


class AcceptedApproachTests(unittest.TestCase):
    def test_a_marker_above_centre_approves_a_move_toward_it(self) -> None:
        # Marker centred horizontally, above the principal point (smaller v). At
        # yaw 0 that is body forward, which is world east; north stays ~0.
        decision = _plan(_marker_frame((150, 70, 20, 20)))  # centre (160, 80)

        self.assertTrue(decision.accepted)
        self.assertIsNotNone(decision.waypoint)
        self.assertGreater(decision.waypoint.east_m, 1.0)
        self.assertAlmostEqual(decision.waypoint.north_m, 0.0, delta=0.6)
        self.assertEqual(decision.waypoint.target_altitude_m, 5.0)

    def test_the_move_is_measured_from_the_vehicles_current_position(self) -> None:
        # Same marker, but the vehicle is already 10 m north; the approved move
        # must carry that offset, not restart from the launch origin.
        at_origin = _plan(_marker_frame((150, 70, 20, 20)))
        shifted = _plan(_marker_frame((150, 70, 20, 20)), vehicle_north_m=10.0)

        self.assertAlmostEqual(shifted.waypoint.north_m - at_origin.waypoint.north_m, 10.0, delta=0.01)
        self.assertAlmostEqual(shifted.waypoint.east_m, at_origin.waypoint.east_m, delta=0.01)

    def test_a_marker_left_of_centre_approves_a_move_north(self) -> None:
        # Image left (smaller u) is body left, which at yaw 0 is world north.
        decision = _plan(_marker_frame((70, 110, 20, 20)))  # centre (80, 120)

        self.assertTrue(decision.accepted)
        self.assertGreater(decision.waypoint.north_m, 1.0)
        self.assertAlmostEqual(decision.waypoint.east_m, 0.0, delta=0.6)


class FailClosedTests(unittest.TestCase):
    def test_an_empty_field_proposes_no_move(self) -> None:
        decision = _plan(_grey_frame())

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)
        self.assertIsNotNone(decision.refusal_reason)

    def test_a_missing_frame_proposes_no_move(self) -> None:
        decision = _plan(None)

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)

    def test_too_much_tilt_refuses_the_flat_ground_projection(self) -> None:
        decision = _plan(_marker_frame((150, 70, 20, 20)), tilt_deg=25.0)

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)

    def test_an_unknown_altitude_proposes_no_move(self) -> None:
        for altitude in (0.0, -3.0, float("nan"), float("inf")):
            with self.subTest(altitude=altitude):
                decision = _plan(_marker_frame((150, 70, 20, 20)), altitude_agl_m=altitude)
                self.assertFalse(decision.accepted)

    def test_a_stale_frame_is_not_chased(self) -> None:
        # Captured fresh but read past its max age; the target is stale, not usable.
        stale = _marker_frame((150, 70, 20, 20), captured_at=_NOW - timedelta(seconds=2))
        decision = _plan(stale, now=_NOW)

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)

    def test_a_target_outside_the_geofence_is_refused_by_the_gate(self) -> None:
        # The vehicle sits just inside the fence's north edge, wherever the
        # active contract puts it; a marker projecting further north puts the
        # target outside. Hardcoding 29 m tested this only while the fence was a
        # 30 m box — widen the contract and the vehicle starts well inside it,
        # so the refusal this test is named after quietly stops happening.
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        assert profile.allowed_geofence is not None
        north_edge_m = max(north for north, _east in profile.allowed_geofence.vertices_m)
        decision = _plan(
            _marker_frame((150, 70, 20, 20)), yaw_deg=90.0, vehicle_north_m=north_edge_m - 1.0
        )

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)
        self.assertIsNotNone(decision.refusal_reason)

    def test_a_fix_too_uncertain_to_chase_is_refused(self) -> None:
        # A tiny uncertainty budget stands in for a target seen from very high;
        # the reaction refuses rather than move toward a loose fix.
        decision = _plan(_marker_frame((150, 70, 20, 20)), max_uncertainty_m=0.001)

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.waypoint)


if __name__ == "__main__":
    unittest.main()
