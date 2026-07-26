"""Private calibrated five-landmark head-pose adapter; no heuristic fallback."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .face_alignment import ScrfdFaceCandidate
from .face_quality import FacePoseEstimate


_MODEL_POINTS = ((-30.0, 35.0, -30.0), (30.0, 35.0, -30.0), (0.0, 0.0, 0.0), (-25.0, -35.0, -20.0), (25.0, -35.0, -20.0))


@dataclass(frozen=True)
class FiveLandmarkCameraCalibration:
    calibration_version: str
    width_px: int
    height_px: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float
    distortion: tuple[float, float, float, float, float]
    max_reprojection_error_px: float

    def __post_init__(self) -> None:
        if not isinstance(self.calibration_version, str) or not self.calibration_version.strip() or type(self.width_px) is not int or type(self.height_px) is not int or self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("Face pose calibration identity and dimensions are invalid.")
        values = (self.fx_px, self.fy_px, self.cx_px, self.cy_px, *self.distortion, self.max_reprojection_error_px)
        if len(self.distortion) != 5 or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not isfinite(x) for x in values) or self.fx_px <= 0 or self.fy_px <= 0 or self.max_reprojection_error_px <= 0:
            raise ValueError("Face pose calibration parameters are invalid.")


class CalibratedFiveLandmarkPoseAdapter:
    def __init__(self, calibration: FiveLandmarkCameraCalibration) -> None:
        if not isinstance(calibration, FiveLandmarkCameraCalibration):
            raise ValueError("A five-landmark camera calibration is required.")
        self._calibration = calibration

    def estimate_bgr(self, image: object, candidate: ScrfdFaceCandidate, *, calibration_version: str) -> FacePoseEstimate:
        try:
            import cv2
            import numpy
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("Face pose estimation requires OpenCV and NumPy.") from error
        c = self._calibration
        if calibration_version != c.calibration_version or not isinstance(image, numpy.ndarray) or image.dtype != numpy.uint8 or image.shape != (c.height_px, c.width_px, 3) or not isinstance(candidate, ScrfdFaceCandidate):
            raise ValueError("Face pose input is not bound to the calibrated camera.")
        points = numpy.asarray(candidate.landmarks_xy, dtype=numpy.float64)
        if not numpy.isfinite(points).all() or (points[:, 0] < 0).any() or (points[:, 1] < 0).any() or (points[:, 0] >= c.width_px).any() or (points[:, 1] >= c.height_px).any():
            raise ValueError("Face landmarks are invalid for pose estimation.")
        matrix = numpy.array(((c.fx_px, 0, c.cx_px), (0, c.fy_px, c.cy_px), (0, 0, 1)), dtype=numpy.float64)
        ok, rotation, translation = cv2.solvePnP(numpy.asarray(_MODEL_POINTS), points, matrix, numpy.asarray(c.distortion), flags=cv2.SOLVEPNP_EPNP)
        if not ok:
            raise ValueError("Face pose solver failed.")
        projected, _ = cv2.projectPoints(numpy.asarray(_MODEL_POINTS), rotation, translation, matrix, numpy.asarray(c.distortion))
        if float(numpy.linalg.norm(projected.reshape(5, 2) - points, axis=1).max()) > c.max_reprojection_error_px:
            raise ValueError("Face pose reprojection error exceeds calibration limit.")
        rotation_matrix, _ = cv2.Rodrigues(rotation)
        yaw = float(numpy.degrees(numpy.arctan2(rotation_matrix[1, 0], rotation_matrix[0, 0])))
        pitch = float(numpy.degrees(numpy.arctan2(-rotation_matrix[2, 0], numpy.hypot(rotation_matrix[2, 1], rotation_matrix[2, 2]))))
        return FacePoseEstimate(yaw, pitch)
