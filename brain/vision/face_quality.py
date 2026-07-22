"""Deterministic P1 face-quality gate for private 1:1 verification.

This gate operates only on adapter-produced scalar metrics. It does not retain
face pixels, landmarks, templates or embeddings, and a failed gate is always a
fail-closed signal for the verification coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from .face_verification import FaceQuality


class FaceQualityReason(str, Enum):
    ACCEPTED = "accepted"
    FACE_TOO_SMALL = "face_too_small"
    BLURRED = "blurred"
    UNDEREXPOSED = "underexposed"
    OVEREXPOSED = "overexposed"
    POSE_OUT_OF_BOUNDS = "pose_out_of_bounds"


@dataclass(frozen=True)
class FaceQualityMetrics:
    """Scalar metrics produced by a detector/alignment adapter, never pixels."""

    face_width_px: int
    face_height_px: int
    blur_variance: float
    mean_luma: float
    yaw_degrees: float
    pitch_degrees: float
    roll_degrees: float

    def __post_init__(self) -> None:
        if type(self.face_width_px) is not int or type(self.face_height_px) is not int or self.face_width_px <= 0 or self.face_height_px <= 0:
            raise ValueError("Face quality dimensions must be positive integers.")
        for value in (self.blur_variance, self.mean_luma, self.yaw_degrees, self.pitch_degrees, self.roll_degrees):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise ValueError("Face quality metrics must be finite numbers.")


@dataclass(frozen=True)
class FaceQualityAssessment:
    """Versioned-threshold outcome suitable for private verification audit data."""

    quality: FaceQuality
    reason: FaceQualityReason
    threshold_version: str
    score: float | None


class FaceQualityGate:
    """Simple, explainable blur/light/pose quality gate for the P1 MVP."""

    def __init__(
        self,
        *,
        threshold_version: str,
        minimum_face_px: int,
        minimum_blur_variance: float,
        minimum_luma: float,
        maximum_luma: float,
        maximum_yaw_degrees: float,
        maximum_pitch_degrees: float,
        maximum_roll_degrees: float,
    ) -> None:
        if not isinstance(threshold_version, str) or not threshold_version.strip():
            raise ValueError("Face quality threshold version is required.")
        if type(minimum_face_px) is not int or minimum_face_px <= 0:
            raise ValueError("Face quality minimum_face_px must be positive.")
        numeric = (minimum_blur_variance, minimum_luma, maximum_luma, maximum_yaw_degrees, maximum_pitch_degrees, maximum_roll_degrees)
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) for value in numeric):
            raise ValueError("Face quality thresholds must be finite numbers.")
        if minimum_blur_variance < 0 or minimum_luma < 0 or maximum_luma > 255 or minimum_luma >= maximum_luma:
            raise ValueError("Face quality blur/luma thresholds are invalid.")
        if any(value < 0 for value in (maximum_yaw_degrees, maximum_pitch_degrees, maximum_roll_degrees)):
            raise ValueError("Face quality pose thresholds must be non-negative.")
        self._version = threshold_version
        self._minimum_face_px = minimum_face_px
        self._minimum_blur = float(minimum_blur_variance)
        self._minimum_luma = float(minimum_luma)
        self._maximum_luma = float(maximum_luma)
        self._maximum_yaw = float(maximum_yaw_degrees)
        self._maximum_pitch = float(maximum_pitch_degrees)
        self._maximum_roll = float(maximum_roll_degrees)

    def assess(self, metrics: FaceQualityMetrics) -> FaceQualityAssessment:
        """Return the first deterministic fail-closed reason or an explainable score."""
        if not isinstance(metrics, FaceQualityMetrics):
            raise ValueError("Face quality gate requires FaceQualityMetrics.")
        reason = self._failure(metrics)
        if reason is not None:
            return FaceQualityAssessment(FaceQuality.FAILED, reason, self._version, None)
        size_score = min(metrics.face_width_px, metrics.face_height_px) / self._minimum_face_px
        blur_score = metrics.blur_variance / self._minimum_blur if self._minimum_blur else 1.0
        center_luma = (self._minimum_luma + self._maximum_luma) / 2
        luma_score = 1 - abs(metrics.mean_luma - center_luma) / ((self._maximum_luma - self._minimum_luma) / 2)
        pose_score = min(
            1 - abs(metrics.yaw_degrees) / max(self._maximum_yaw, 1),
            1 - abs(metrics.pitch_degrees) / max(self._maximum_pitch, 1),
            1 - abs(metrics.roll_degrees) / max(self._maximum_roll, 1),
        )
        score = min(1.0, max(0.0, (min(1.0, size_score) + min(1.0, blur_score) + luma_score + pose_score) / 4))
        return FaceQualityAssessment(FaceQuality.PASSED, FaceQualityReason.ACCEPTED, self._version, score)

    def _failure(self, metrics: FaceQualityMetrics) -> FaceQualityReason | None:
        if min(metrics.face_width_px, metrics.face_height_px) < self._minimum_face_px:
            return FaceQualityReason.FACE_TOO_SMALL
        if metrics.blur_variance < self._minimum_blur:
            return FaceQualityReason.BLURRED
        if metrics.mean_luma < self._minimum_luma:
            return FaceQualityReason.UNDEREXPOSED
        if metrics.mean_luma > self._maximum_luma:
            return FaceQualityReason.OVEREXPOSED
        if abs(metrics.yaw_degrees) > self._maximum_yaw or abs(metrics.pitch_degrees) > self._maximum_pitch or abs(metrics.roll_degrees) > self._maximum_roll:
            return FaceQualityReason.POSE_OUT_OF_BOUNDS
        return None
