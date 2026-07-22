"""Observation-only Vision Core domain contracts and adapters."""

from brain.vision.contracts import (
    CAMERA_FRAME_V1,
    DETECTION_RESULT_V1,
    VISION_HEALTH_V1,
    BoundingBox,
    CameraFrame,
    Detection,
    DetectionResult,
    FrameValidation,
    FrameSequenceLedger,
    ResultState,
    VisionContractError,
    VisionHealth,
)
from brain.vision.face_verification import (
    FACE_VERIFICATION_V1,
    BiometricConsent,
    ConsentState,
    FaceQuality,
    FaceVerification,
    FaceVerificationError,
    LivenessResult,
    MatchResult,
)

__all__ = (
    "CAMERA_FRAME_V1",
    "DETECTION_RESULT_V1",
    "VISION_HEALTH_V1",
    "BoundingBox",
    "CameraFrame",
    "Detection",
    "DetectionResult",
    "FrameValidation",
    "FrameSequenceLedger",
    "ResultState",
    "VisionContractError",
    "VisionHealth",
    "FACE_VERIFICATION_V1",
    "BiometricConsent",
    "ConsentState",
    "FaceQuality",
    "FaceVerification",
    "FaceVerificationError",
    "LivenessResult",
    "MatchResult",
)
