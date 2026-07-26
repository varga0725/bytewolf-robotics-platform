"""Observation-only Vision Core domain contracts and adapters.

Two contract layers live here and are deliberately distinct:

* the **internal** Vision Core contracts re-exported below (``CameraFrame``,
  ``DetectionResult``, ``VisionHealth`` and friends), which the pipeline uses
  and which carry the producer's full frame detail;
* the **shared** consumer-facing contracts in ``brain.vision.canonical``
  (``DetectionEvent``, ``VisionSummary``, ``VisionHealth`` as published JSON),
  which is all an external consumer such as the Cognitive Runtime reads.

``brain.vision.canonical_serialize`` maps the first onto the second, so the
Vision internals can evolve without breaking a consumer. Note that each layer
defines its own ``VisionContractError``; the name re-exported here is the
internal one.
"""

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
from brain.vision.events import (
    DetectionEvent,
    TrackedObject,
    VideoArtifactRef,
    VisionSummary,
    canonical_from_detection_result,
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
    "DetectionEvent",
    "TrackedObject",
    "VideoArtifactRef",
    "VisionSummary",
    "canonical_from_detection_result",
)
