"""A calibration must be a trustworthy measurement or it does not load at all."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.perception.camera_calibration import (
    CameraCalibrationError,
    load_camera_calibration,
)


def _valid_pinhole() -> dict:
    return {
        "contract_version": "v0.1",
        "camera_id": "down_rgb",
        "device": "test pinhole cam",
        "projection_model": "pinhole",
        "image_size": {"width": 1920, "height": 1080},
        "intrinsics": {"fx": 1400.0, "fy": 1400.0, "cx": 960.0, "cy": 540.0},
        "distortion": {"model": "plumb_bob", "coefficients": [0.01, -0.02, 0.0, 0.0, 0.0]},
        "reprojection_error_px": 0.4,
        "provenance": {
            "status": "measured",
            "method": "OpenCV checkerboard, 9x6, 25mm, 20 poses",
            "captured_at": "2026-07-24T10:00:00+00:00",
            "target": "9x6 checkerboard",
        },
    }


def _valid_fisheye() -> dict:
    document = _valid_pinhole()
    document.update(
        camera_id="front_rgb",
        device="Hawkeye 4K Split V5",
        projection_model="fisheye",
        image_size={"width": 3840, "height": 2160},
        intrinsics={"fx": 1200.0, "fy": 1200.0, "cx": 1920.0, "cy": 1080.0},
        distortion={"model": "kannala_brandt", "coefficients": [0.05, -0.01, 0.002, -0.0004]},
    )
    return document


_KEEP_ALIVE: list[TemporaryDirectory] = []


def _write(document: dict) -> Path:
    directory = TemporaryDirectory()
    _KEEP_ALIVE.append(directory)  # cleaned up when the process exits
    path = Path(directory.name) / "calibration.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class LoadTests(unittest.TestCase):
    def test_a_valid_pinhole_calibration_yields_consumable_intrinsics(self) -> None:
        calibration = load_camera_calibration(_write(_valid_pinhole()))

        self.assertFalse(calibration.is_fisheye)
        intrinsics = calibration.pinhole_intrinsics()
        self.assertEqual((intrinsics.width, intrinsics.height), (1920, 1080))
        self.assertGreater(intrinsics.horizontal_fov_rad, 0.0)

    def test_a_fisheye_refuses_to_become_pinhole_intrinsics(self) -> None:
        calibration = load_camera_calibration(_write(_valid_fisheye()))

        self.assertTrue(calibration.is_fisheye)
        with self.assertRaisesRegex(CameraCalibrationError, "undistort"):
            calibration.pinhole_intrinsics()

    def test_a_schema_violation_fails_closed(self) -> None:
        document = _valid_pinhole()
        del document["intrinsics"]
        with self.assertRaises(CameraCalibrationError):
            load_camera_calibration(_write(document))

    def test_a_non_finite_intrinsic_fails_closed(self) -> None:
        document = _valid_pinhole()
        document["intrinsics"]["fx"] = float("inf")
        with self.assertRaisesRegex(CameraCalibrationError, "finite"):
            load_camera_calibration(_write(document))

    def test_a_principal_point_outside_the_image_fails_closed(self) -> None:
        document = _valid_pinhole()
        document["intrinsics"]["cx"] = 5000.0
        with self.assertRaisesRegex(CameraCalibrationError, "principal point"):
            load_camera_calibration(_write(document))

    def test_a_poor_reprojection_error_is_not_trusted(self) -> None:
        document = _valid_pinhole()
        document["reprojection_error_px"] = 3.0
        with self.assertRaisesRegex(CameraCalibrationError, "exceeds"):
            load_camera_calibration(_write(document))

    def test_distortion_none_may_not_carry_coefficients(self) -> None:
        document = _valid_pinhole()
        document["distortion"] = {"model": "none", "coefficients": [0.1]}
        with self.assertRaises(CameraCalibrationError):
            load_camera_calibration(_write(document))

    def test_a_real_distortion_model_needs_a_coefficient(self) -> None:
        document = _valid_pinhole()
        document["distortion"] = {"model": "plumb_bob", "coefficients": []}
        with self.assertRaises(CameraCalibrationError):
            load_camera_calibration(_write(document))


if __name__ == "__main__":
    unittest.main()
