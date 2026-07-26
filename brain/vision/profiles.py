"""Explicit camera-profile registry for P0 Vision Core adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class CameraProfileId(str, Enum):
    FRONT_DETECTION = "front_detection"
    FRONT_TRACKING = "front_tracking"
    FRONT_BIOMETRICS = "front_biometrics"
    FRONT_GESTURE = "front_gesture"
    DOWN_PRECISION_LANDING = "down_precision_landing"
    DOWN_ARUCO = "down_aruco"
    DEPTH_OBSTACLE = "depth_obstacle"
    RECORDED_BENCHMARK = "recorded_benchmark"
    GAZEBO_SIMULATION = "gazebo_simulation"


@dataclass(frozen=True)
class CameraProfile:
    """Static adapter routing metadata; it grants no control capability."""

    camera_id: str
    purpose: str
    requires_depth: bool = False


CAMERA_PROFILES = MappingProxyType({
    CameraProfileId.FRONT_DETECTION: CameraProfile("front-rgb", "general object detection"),
    CameraProfileId.FRONT_TRACKING: CameraProfile("front-rgb", "multi-object tracking"),
    CameraProfileId.FRONT_BIOMETRICS: CameraProfile("front-rgb", "opt-in face verification"),
    CameraProfileId.FRONT_GESTURE: CameraProfile("front-rgb", "gesture and pose observation"),
    CameraProfileId.DOWN_PRECISION_LANDING: CameraProfile("down-rgb", "precision landing target observation"),
    CameraProfileId.DOWN_ARUCO: CameraProfile("down-rgb", "ArUco marker observation"),
    CameraProfileId.DEPTH_OBSTACLE: CameraProfile("depth-front", "depth obstacle observation", requires_depth=True),
    CameraProfileId.RECORDED_BENCHMARK: CameraProfile("recorded-fixture", "offline reproducible benchmark"),
    CameraProfileId.GAZEBO_SIMULATION: CameraProfile("gazebo-camera", "Gazebo simulation ingest"),
})
