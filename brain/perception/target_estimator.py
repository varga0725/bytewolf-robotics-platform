"""Project a down-camera detection to a ground target the mission can react to.

This is the relative-position stage of the V1 perception flow. A down-facing
camera sees a target on the ground; combined with the vehicle's own altitude and
GPS, a detection's pixel position projects to a local north/east offset and an
absolute fix. The result is a versioned, fail-closed target observation -- data,
never a command. The mission logic may act on it only when it is valid and fresh,
and only by proposing a step that the Safety Kernel re-checks.

A monocular camera cannot measure range on its own, so this uses the one thing
that makes it possible here: the vehicle knows how high it is. Under a near-nadir
view over flat ground, altitude turns a pixel offset into a metric ground offset.
When that assumption does not hold -- the altitude is unknown, or the vehicle is
tilted too far for the flat-ground projection -- the observation fails closed to
invalid rather than inventing a position.

The pixel-to-north/east sign follows a documented convention and is checked for
internal consistency by unit tests; a down-camera SITL scenario with a marker at
a known world position is what confirms it against ground truth, the same way the
lidar frame sign was pinned. The uncertainty grows with altitude, because the
same pixel error projects to a larger ground error the higher the vehicle flies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
from math import cos, hypot, isfinite, radians, sin, tan
from pathlib import Path
from typing import Any

import jsonschema

from brain.perception.detector import DetectionResult, DetectorState


TARGET_CONTRACT_VERSION = "v0.1"
TARGET_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared/schemas/perception/target_observation_v0_1.schema.json"
)
DEFAULT_MAX_AGE_S = 0.5
DEFAULT_MAX_TILT_DEG = 10.0
DEFAULT_PIXEL_UNCERTAINTY_PX = 8.0

_EARTH_RADIUS_M = 6_371_000.0
_DEGREES_PER_RADIAN = 57.29577951308232


class TargetEstimationError(ValueError):
    """Raised when a target observation cannot be read as the contract requires."""


@dataclass(frozen=True)
class CameraIntrinsics:
    """The pinhole parameters a projection needs, derived from FOV and size."""

    width: int
    height: int
    horizontal_fov_rad: float

    @property
    def focal_length_px(self) -> float:
        return (self.width / 2) / tan(self.horizontal_fov_rad / 2)

    @property
    def principal_point(self) -> tuple[float, float]:
        return (self.width / 2, self.height / 2)


# Measured from PX4's x500_mono_cam_down: the mono_cam sensor (1280x960, 1.74 rad
# horizontal FOV) mounted pitched 90 degrees down.
NADIR_MONO_CAM_DOWN = CameraIntrinsics(width=1280, height=960, horizontal_fov_rad=1.74)


@dataclass(frozen=True)
class GlobalFix:
    latitude_deg: float
    longitude_deg: float


class TargetState(Enum):
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    STALE = "stale"

    @property
    def usable(self) -> bool:
        return self is TargetState.VALID


@dataclass(frozen=True)
class TargetObservation:
    """A ground target projected from a detection, fail-closed like every observation."""

    captured_at: datetime
    max_age_s: float
    declared_validity: str
    label: str | None
    confidence: float | None
    offset_north_m: float | None
    offset_east_m: float | None
    range_m: float | None
    horizontal_uncertainty_m: float | None
    global_fix: GlobalFix | None
    source: str | None

    def age_s(self, now: datetime) -> float:
        return max(0.0, (_utc(now) - self.captured_at).total_seconds())

    def state(self, now: datetime) -> TargetState:
        if self.declared_validity == "missing":
            return TargetState.MISSING
        if self.declared_validity == "invalid":
            return TargetState.INVALID
        if self.age_s(now) > self.max_age_s:
            return TargetState.STALE
        return TargetState.VALID

    def usable_offset_m(self, now: datetime) -> tuple[float, float]:
        """Return the local north/east offset only if it may be acted on."""
        state = self.state(now)
        if not state.usable:
            raise TargetEstimationError(f"Target observation is {state.value} and must not be acted on.")
        assert self.offset_north_m is not None and self.offset_east_m is not None
        return self.offset_north_m, self.offset_east_m

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "contract_version": TARGET_CONTRACT_VERSION,
            "captured_at": self.captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "max_age_s": self.max_age_s,
            "validity": self.declared_validity,
            "frame": "local_ned",
        }
        if self.source is not None:
            document["source"] = self.source
        if self.declared_validity == "valid":
            target: dict[str, Any] = {
                "label": self.label,
                "confidence": self.confidence,
                "offset_north_m": self.offset_north_m,
                "offset_east_m": self.offset_east_m,
                "range_m": self.range_m,
                "horizontal_uncertainty_m": self.horizontal_uncertainty_m,
            }
            if self.global_fix is not None:
                target["global_position"] = {
                    "latitude_deg": self.global_fix.latitude_deg,
                    "longitude_deg": self.global_fix.longitude_deg,
                }
            document["target"] = target
        return document


class GroundTargetEstimator:
    """Turn a down-camera detection into a fail-closed ground target observation."""

    def __init__(
        self,
        intrinsics: CameraIntrinsics = NADIR_MONO_CAM_DOWN,
        *,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_tilt_deg: float = DEFAULT_MAX_TILT_DEG,
        pixel_uncertainty_px: float = DEFAULT_PIXEL_UNCERTAINTY_PX,
        source: str | None = None,
    ) -> None:
        self._intrinsics = intrinsics
        self._max_age_s = max_age_s
        self._max_tilt_deg = max_tilt_deg
        self._pixel_uncertainty_px = pixel_uncertainty_px
        self._source = source

    def estimate(
        self,
        detection_result: DetectionResult,
        *,
        altitude_agl_m: float,
        now: datetime,
        yaw_deg: float = 0.0,
        tilt_deg: float = 0.0,
        global_position: GlobalFix | None = None,
    ) -> TargetObservation:
        """Project the most confident detection to a ground target, or fail closed."""
        state = detection_result.state(now)
        if state is DetectorState.MISSING:
            return self._empty("missing")
        if state is not DetectorState.VALID:
            # A stale or invalid detection cannot yield a trustworthy target.
            return self._empty("invalid")

        detections = detection_result.usable_detections(now)
        if not detections:
            return self._empty("missing")
        if not (isfinite(altitude_agl_m) and altitude_agl_m > 0.0):
            return self._empty("invalid")
        if not isfinite(tilt_deg) or abs(tilt_deg) > self._max_tilt_deg:
            # The flat-ground nadir projection only holds near level.
            return self._empty("invalid")

        target = max(detections, key=lambda detection: detection.confidence)
        centre_u = target.bbox.x + target.bbox.width / 2
        centre_v = target.bbox.y + target.bbox.height / 2
        north_m, east_m = self._ground_offset(centre_u, centre_v, altitude_agl_m, yaw_deg)
        ground_distance_m = hypot(north_m, east_m)
        range_m = hypot(altitude_agl_m, ground_distance_m)
        uncertainty_m = altitude_agl_m * self._pixel_uncertainty_px / self._intrinsics.focal_length_px
        global_fix = self._global_fix(north_m, east_m, global_position)

        return TargetObservation(
            captured_at=_utc(detection_result.captured_at),
            max_age_s=self._max_age_s,
            declared_validity="valid",
            label=target.label,
            confidence=target.confidence,
            offset_north_m=north_m,
            offset_east_m=east_m,
            range_m=range_m,
            horizontal_uncertainty_m=uncertainty_m,
            global_fix=global_fix,
            source=self._source,
        )

    def _ground_offset(
        self, centre_u: float, centre_v: float, altitude_agl_m: float, yaw_deg: float
    ) -> tuple[float, float]:
        principal_u, principal_v = self._intrinsics.principal_point
        focal = self._intrinsics.focal_length_px
        # Image-to-body mapping for this down-camera mount, confirmed against
        # Gazebo ground truth (an earlier "obvious" mapping was rotated 90 deg and
        # the SITL scenario caught it): image up (smaller v) is body forward,
        # image left (smaller u) is body left. Magnitudes are altitude / focal.
        body_forward_m = altitude_agl_m * (principal_v - centre_v) / focal
        body_left_m = altitude_agl_m * (principal_u - centre_u) / focal
        # yaw_deg is the Gazebo ENU heading: 0 means the body forward axis points
        # world +X (east), 90 means it points +Y (north).
        yaw = radians(yaw_deg)
        east_m = body_forward_m * cos(yaw) - body_left_m * sin(yaw)
        north_m = body_forward_m * sin(yaw) + body_left_m * cos(yaw)
        return north_m, east_m

    def _global_fix(
        self, north_m: float, east_m: float, origin: GlobalFix | None
    ) -> GlobalFix | None:
        if origin is None:
            return None
        # Same local-to-global convention as brain/navigation/waypoints.py.
        latitude_delta = north_m / _EARTH_RADIUS_M * _DEGREES_PER_RADIAN
        longitude_delta = east_m / (_EARTH_RADIUS_M * cos(radians(origin.latitude_deg))) * _DEGREES_PER_RADIAN
        return GlobalFix(origin.latitude_deg + latitude_delta, origin.longitude_deg + longitude_delta)

    def _empty(self, validity: str) -> TargetObservation:
        return TargetObservation(
            captured_at=datetime.now(UTC),
            max_age_s=self._max_age_s,
            declared_validity=validity,
            label=None,
            confidence=None,
            offset_north_m=None,
            offset_east_m=None,
            range_m=None,
            horizontal_uncertainty_m=None,
            global_fix=None,
            source=self._source,
        )


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    try:
        return json.loads(TARGET_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise TargetEstimationError(
            f"Cannot read the target schema '{TARGET_SCHEMA_PATH}': {error.strerror}."
        ) from error


def validate_target_document(document: object) -> None:
    """Check a target observation document against the versioned contract."""
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise TargetEstimationError(f"Target observation rejected at '{location}': {error.message}") from error


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise TargetEstimationError("A target time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
