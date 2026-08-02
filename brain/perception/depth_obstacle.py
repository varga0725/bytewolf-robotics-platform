"""Convert calibrated depth-camera frames into fail-closed obstacle evidence.

RGB detections can strengthen a semantic world model, but their absence never
proves free space.  A depth frame is different: every valid range sample can
contribute to the existing body-FRD obstacle contract.  This adapter deliberately
accepts depth encodings only and leaves all bearings outside the camera FOV
unobserved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil, floor, isfinite
import struct

from brain.perception.camera_frame import CameraFrame, FrameEncoding


OBSTACLE_FRAME = "body_frd"


class DepthObstacleError(ValueError):
    """Raised when a frame cannot truthfully provide metric obstacle evidence."""


def depth_obstacle_observation(
    frame: CameraFrame,
    *,
    vehicle_id: str,
    horizontal_fov_deg: float,
    min_range_m: float,
    max_range_m: float,
    sector_width_deg: float = 15.0,
    max_age_s: float = 0.3,
) -> dict:
    """Return a body-FRD obstacle observation from a metric depth frame.

    Invalid/no-return pixels never become ``clear``.  They mark their bearing
    ``unobserved``; only finite positive ranges at or beyond sensor max range
    can establish clear coverage.
    """
    _validate(frame, horizontal_fov_deg, min_range_m, max_range_m, sector_width_deg, max_age_s)
    values = _depth_values_m(frame)
    sector_count = ceil(horizontal_fov_deg / sector_width_deg)
    buckets: list[list[float]] = [[] for _ in range(sector_count)]
    observed: list[bool] = [False] * sector_count
    for column in range(frame.width):
        yaw_deg = ((column + 0.5) / frame.width - 0.5) * horizontal_fov_deg
        index = min(sector_count - 1, max(0, floor((yaw_deg + horizontal_fov_deg / 2.0) / sector_width_deg)))
        column_values = [values[row * frame.width + column] for row in range(frame.height)]
        valid = [value for value in column_values if isfinite(value) and value > 0.0]
        if not valid:
            continue
        observed[index] = True
        buckets[index].extend(valid)
    sectors = []
    for index in range(sector_count):
        width = min(sector_width_deg, horizontal_fov_deg - index * sector_width_deg)
        yaw = -horizontal_fov_deg / 2.0 + index * sector_width_deg + width / 2.0
        sector: dict[str, object] = {"yaw_deg": round(yaw, 6), "width_deg": width}
        if not observed[index]:
            sector["coverage"] = "unobserved"
        else:
            returns = [value for value in buckets[index] if min_range_m <= value < max_range_m]
            if returns:
                sector.update({"coverage": "measured", "distance_m": round(min(returns), 6)})
            else:
                sector["coverage"] = "clear"
        sectors.append(sector)
    return {
        "contract_version": "v0.1", "kind": "obstacle", "vehicle_id": vehicle_id,
        "observed_at": frame.utc_captured_at().isoformat().replace("+00:00", "Z"),
        "max_age_s": max_age_s, "validity": "valid", "source": f"depth camera {frame.sensor_id}",
        "payload": {
            "frame": OBSTACLE_FRAME,
            "sensor": {"id": frame.sensor_id, "min_range_m": min_range_m, "max_range_m": max_range_m},
            "sectors": sectors,
        },
    }


def _validate(frame: CameraFrame, fov: float, minimum: float, maximum: float, width: float, age: float) -> None:
    if frame.encoding not in (FrameEncoding.DEPTH16, FrameEncoding.DEPTH32F):
        raise DepthObstacleError("Obstacle depth evidence requires depth16 or depth32f, never RGB.")
    if not frame.is_well_formed():
        raise DepthObstacleError("Depth frame bytes do not match its declared shape.")
    for name, value in (("horizontal_fov_deg", fov), ("min_range_m", minimum), ("max_range_m", maximum), ("sector_width_deg", width), ("max_age_s", age)):
        if not isfinite(value) or value <= 0.0:
            raise DepthObstacleError(f"{name} must be positive and finite.")
    if fov > 360.0 or width > fov or minimum >= maximum:
        raise DepthObstacleError("Depth FOV, sector width, or range bounds are invalid.")


def _depth_values_m(frame: CameraFrame) -> tuple[float, ...]:
    count = frame.width * frame.height
    if frame.encoding is FrameEncoding.DEPTH16:
        return tuple(value / 1000.0 for (value,) in struct.iter_unpack("<H", frame.data))
    return tuple(value for (value,) in struct.iter_unpack("<f", frame.data))


__all__ = ["DepthObstacleError", "depth_obstacle_observation"]
