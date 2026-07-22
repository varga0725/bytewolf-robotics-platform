"""Private SCRFD-compatible face candidate and five-point alignment utility.

The detector adapter is intentionally not implemented here: it must supply a
validated ``ScrfdFaceCandidate`` from an explicitly provisioned local model.
This module only transforms an in-memory BGR frame into an in-memory ArcFace
crop.  It does not persist pixels or expose a public Vision contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


_ARCFACE_112_LANDMARKS = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


def _finite_number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise ValueError("Face candidate coordinates must be finite numbers.")
    return float(value)


@dataclass(frozen=True)
class ScrfdFaceCandidate:
    """Private detector output with SCRFD's ordered five facial landmarks.

    Landmark order is left eye, right eye, nose, left mouth corner, right
    mouth corner from the subject's perspective.  The candidate carries model
    provenance so downstream private verification cannot silently mix models.
    """

    model_id: str
    model_version: str
    confidence: float
    bounds_xyxy: tuple[float, float, float, float]
    landmarks_xy: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip() or not isinstance(self.model_version, str) or not self.model_version.strip():
            raise ValueError("SCRFD face candidate requires model ID and version.")
        confidence = _finite_number(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("SCRFD face confidence must be between zero and one.")
        if not isinstance(self.bounds_xyxy, tuple) or len(self.bounds_xyxy) != 4:
            raise ValueError("SCRFD face bounds must be an xyxy tuple.")
        left, top, right, bottom = tuple(_finite_number(value) for value in self.bounds_xyxy)
        if right <= left or bottom <= top:
            raise ValueError("SCRFD face bounds must have positive area.")
        if not isinstance(self.landmarks_xy, tuple) or len(self.landmarks_xy) != 5:
            raise ValueError("SCRFD face candidate requires exactly five ordered landmarks.")
        normalized_landmarks: list[tuple[float, float]] = []
        for landmark in self.landmarks_xy:
            if not isinstance(landmark, tuple) or len(landmark) != 2:
                raise ValueError("SCRFD face landmarks must be xy tuples.")
            normalized_landmarks.append((_finite_number(landmark[0]), _finite_number(landmark[1])))
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "bounds_xyxy", (left, top, right, bottom))
        object.__setattr__(self, "landmarks_xy", tuple(normalized_landmarks))


def align_five_point_bgr(image: object, candidate: ScrfdFaceCandidate) -> object:
    """Return a 112×112 BGR ArcFace crop from one private face candidate.

    The operation is deterministic for a given OpenCV runtime and fails closed
    if input pixels or landmarks cannot yield a stable partial affine transform.
    """
    if not isinstance(candidate, ScrfdFaceCandidate):
        raise ValueError("Face alignment requires a ScrfdFaceCandidate.")
    try:
        import cv2
        import numpy
    except ImportError as error:  # pragma: no cover - deployment guard
        raise RuntimeError("Face alignment requires OpenCV and NumPy in the research runtime.") from error
    if not isinstance(image, numpy.ndarray) or image.dtype != numpy.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Face alignment requires a uint8 BGR image.")
    if image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError("Face alignment requires a non-empty BGR image.")
    source = numpy.asarray(candidate.landmarks_xy, dtype=numpy.float32)
    if (source[:, 0] < 0).any() or (source[:, 1] < 0).any() or (source[:, 0] >= image.shape[1]).any() or (source[:, 1] >= image.shape[0]).any():
        raise ValueError("Face landmarks must be inside the source image.")
    target = numpy.asarray(_ARCFACE_112_LANDMARKS, dtype=numpy.float32)
    matrix, _ = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
    if matrix is None or matrix.shape != (2, 3) or not numpy.isfinite(matrix).all():
        raise ValueError("Face landmarks cannot be aligned to the ArcFace reference crop.")
    aligned = cv2.warpAffine(
        image,
        matrix,
        (112, 112),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    if aligned.shape != (112, 112, 3) or aligned.dtype != numpy.uint8:
        raise RuntimeError("Face alignment produced an invalid ArcFace crop.")
    return aligned
