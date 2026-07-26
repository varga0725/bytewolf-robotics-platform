"""Load a measured camera calibration into the twin, fail-closed.

A camera calibration is a measurement of a physical lens: its intrinsics and its
distortion. This is the only sanctioned path from a bench measurement into the
perception stack, and it refuses anything that is not a trustworthy measurement.

The measurement itself is done off-project, on the bench, with the physical
camera and a checkerboard (standard OpenCV fisheye calibration). Its output is
written into the versioned calibration contract
(shared/schemas/perception/camera_calibration_v0_1.schema.json); this module
validates that contract and turns it into the parameters the perception code
uses. It never fabricates a calibration, and it never lets a fisheye be consumed
by a pinhole projection without being told the caller has undistorted it first --
that is exactly the error a 160-degree Hawkeye would cause in target_estimator.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
from typing import Any

import jsonschema

from brain.perception.target_estimator import CameraIntrinsics


CALIBRATION_CONTRACT_VERSION = "v0.1"
CALIBRATION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "shared/schemas/perception/camera_calibration_v0_1.schema.json"
)
# A calibration whose RMS reprojection error is worse than this is not trusted.
# Sub-pixel is the usual bar for a usable checkerboard calibration; a larger
# value means poor coverage or a wrong target spec, so the loader fails closed.
DEFAULT_MAX_REPROJECTION_ERROR_PX = 1.5


class CameraCalibrationError(ValueError):
    """Raised when a calibration cannot be read as a trustworthy measurement."""


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(CALIBRATION_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class CameraCalibration:
    """A validated, measured camera calibration."""

    camera_id: str
    device: str | None
    projection_model: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    distortion_model: str
    distortion_coefficients: tuple[float, ...]
    reprojection_error_px: float
    method: str
    captured_at: str
    target: str | None

    @property
    def is_fisheye(self) -> bool:
        return self.projection_model == "fisheye"

    def pinhole_intrinsics(self) -> CameraIntrinsics:
        """The intrinsics a pinhole projection (target_estimator) may consume.

        A fisheye calibration must be undistorted before a pinhole projection uses
        it; handing raw fisheye intrinsics to the flat-ground projection would put
        the target in the wrong place. This fails closed on a fisheye rather than
        return intrinsics that look usable but are not.
        """
        if self.is_fisheye:
            raise CameraCalibrationError(
                f"Camera '{self.camera_id}' is a fisheye; undistort the frame with this "
                "calibration before a pinhole projection consumes it."
            )
        horizontal_fov_rad = 2.0 * _atan2(self.width / 2.0, self.fx)
        return CameraIntrinsics(
            width=self.width, height=self.height, horizontal_fov_rad=horizontal_fov_rad
        )


def _atan2(opposite: float, adjacent: float) -> float:
    from math import atan2

    return atan2(opposite, adjacent)


def load_camera_calibration(
    path: Path | str,
    *,
    max_reprojection_error_px: float = DEFAULT_MAX_REPROJECTION_ERROR_PX,
) -> CameraCalibration:
    """Read and validate a camera calibration contract, fail-closed.

    Beyond schema validation this enforces what the schema cannot: finite numbers,
    a principal point inside the image, and a reprojection error under the trusted
    threshold. Any failure raises rather than returning a partial calibration.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CameraCalibrationError(f"Cannot read calibration '{path}': {error}") from error

    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as error:
        raise CameraCalibrationError(f"Calibration does not match the contract: {error.message}") from error

    image = document["image_size"]
    intr = document["intrinsics"]
    width, height = int(image["width"]), int(image["height"])
    fx, fy, cx, cy = (float(intr["fx"]), float(intr["fy"]), float(intr["cx"]), float(intr["cy"]))
    reprojection = float(document["reprojection_error_px"])
    coefficients = tuple(float(value) for value in document["distortion"]["coefficients"])

    for name, value in (("fx", fx), ("fy", fy), ("cx", cx), ("cy", cy), ("reprojection", reprojection)):
        if not isfinite(value):
            raise CameraCalibrationError(f"Calibration '{name}' is not finite.")
    if not all(isfinite(value) for value in coefficients):
        raise CameraCalibrationError("A distortion coefficient is not finite.")
    if cx >= width or cy >= height:
        raise CameraCalibrationError(
            "The principal point falls outside the image; the calibration is wrong for this size."
        )
    if reprojection > max_reprojection_error_px:
        raise CameraCalibrationError(
            f"Reprojection error {reprojection} px exceeds the trusted "
            f"{max_reprojection_error_px} px; the calibration is not usable."
        )

    provenance = document["provenance"]
    return CameraCalibration(
        camera_id=document["camera_id"],
        device=document.get("device"),
        projection_model=document["projection_model"],
        width=width,
        height=height,
        fx=fx,
        fy=fy,
        cx=cx,
        cy=cy,
        distortion_model=document["distortion"]["model"],
        distortion_coefficients=coefficients,
        reprojection_error_px=reprojection,
        method=provenance["method"],
        captured_at=provenance["captured_at"],
        target=provenance.get("target"),
    )
