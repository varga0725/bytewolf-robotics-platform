"""Turn a planar laser scan into an obstacle observation the contract accepts.

This is the perception path of the autonomy roadmap's Phase B: it produces the
``obstacle`` observation family, and nothing more. It never commands, never
touches an actuator, and never decides to move -- it only reports what the
sensor can and cannot see, in the shape a consumer is required to distrust.

Two rules from the contract drive every line here:

* A sector states whether it can speak at all -- ``measured``, ``clear``, or
  ``unobserved`` -- and only a ``measured`` sector may carry a distance. A
  bearing the sensor never swept is ``unobserved``, never ``clear``. The
  ``lidar_2d_v2`` sees 270 degrees, so the 90 degrees behind the vehicle come
  out ``unobserved`` on every scan, which is the contract's way of saying the
  vehicle must not move backward on this evidence.
* The distance is the *nearest* return in the sector, not the average, because
  the closest obstacle is the one that constrains motion.

Bearings need care. A gz laser scan measures angle counter-clockwise from the
vehicle's forward axis, while the obstacle frame is body forward-right-down with
yaw positive clockwise seen from above, so the two run in opposite directions.
The sign is flipped here deliberately and pinned by a unit test; a headless
obstacle scenario with a known obstacle bearing is what confirms the flip
against ground truth. This project has already paid once for trusting a frame by
its label instead of its ground truth -- the wind blew east while it was called
north -- so the mapping is asserted, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import degrees, isfinite, isnan
from statistics import pstdev


OBSTACLE_FRAME = "body_frd"
DEFAULT_SECTOR_WIDTH_DEG = 15.0
DEFAULT_MAX_AGE_S = 0.3

# A return at or past the sensor's own maximum is "nothing here", not an
# obstacle at the edge, so it is treated as clear rather than measured.
_RANGE_MAX_EPSILON_M = 1e-6


class LidarObstacleError(ValueError):
    """Raised when a scan is too malformed to speak about obstacles safely."""


def laser_scan_from_gz_json(message: dict) -> LaserScan:
    """Parse a Gazebo ``LaserScan`` message into a :class:`LaserScan`.

    ``gz topic --json-output`` names fields in camelCase and encodes a
    no-return beam as the string ``"Infinity"``, so a return is read here as a
    float or as one of those sentinels, and anything else is refused rather than
    guessed at.
    """
    try:
        angle_min = float(message["angleMin"])
        angle_step = float(message["angleStep"])
        range_min = float(message["rangeMin"])
        range_max = float(message["rangeMax"])
        raw_ranges = message["ranges"]
    except (KeyError, TypeError, ValueError) as error:
        raise LidarObstacleError(f"Gazebo LaserScan is missing or malforming a field: {error}.") from error
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise LidarObstacleError("Gazebo LaserScan must carry a non-empty ranges array.")
    return LaserScan(
        angle_min_rad=angle_min,
        angle_increment_rad=angle_step,
        ranges_m=tuple(_gz_range(value) for value in raw_ranges),
        range_min_m=range_min,
        range_max_m=range_max,
    )


def _gz_range(value: object) -> float:
    """Read one gz range: a number, or the string sentinels gz emits in JSON."""
    if type(value) in (int, float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text in ("Infinity", "inf", "Inf"):
            return float("inf")
        if text in ("-Infinity", "-inf", "-Inf"):
            return float("-inf")
        if text in ("NaN", "nan"):
            return float("nan")
        try:
            return float(text)
        except ValueError as error:
            raise LidarObstacleError(f"Gazebo LaserScan range '{value}' is not a number.") from error
    raise LidarObstacleError(f"Gazebo LaserScan range {value!r} is neither a number nor a known sentinel.")


@dataclass(frozen=True)
class LaserScan:
    """The minimum a planar lidar must provide, independent of any gz message type.

    ``angle_min_rad`` is the bearing of ``ranges_m[0]`` measured counter-clockwise
    from forward; each subsequent beam is ``angle_increment_rad`` further round.
    """

    angle_min_rad: float
    angle_increment_rad: float
    ranges_m: tuple[float, ...]
    range_min_m: float
    range_max_m: float


@dataclass(frozen=True)
class _SectorSamples:
    """The in-range returns gathered for one sector, before it is classified."""

    beam_count: int = 0
    in_range_distances: tuple[float, ...] = ()


def obstacle_observation(
    scan: LaserScan,
    *,
    vehicle_id: str,
    observed_at: datetime,
    sensor_id: str,
    sector_width_deg: float = DEFAULT_SECTOR_WIDTH_DEG,
    max_age_s: float = DEFAULT_MAX_AGE_S,
) -> dict:
    """Return an obstacle observation document for ``scan``.

    The result is a plain dict deliberately: the caller passes it through
    ``load_observation`` so the same schema that guards every other observation
    also guards this one, instead of this module trusting its own output.
    """
    _validate_scan(scan)
    if not isfinite(sector_width_deg) or not 0.0 < sector_width_deg <= 360.0:
        raise LidarObstacleError("Sector width must be a positive number of degrees up to 360.")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise LidarObstacleError("A scan's observation time must be timezone-aware.")

    samples = _bin_beams_into_sectors(scan, sector_width_deg)
    sector_count = round(360.0 / sector_width_deg)
    sectors = [
        _sector_document(index, sector_width_deg, samples.get(index, _SectorSamples()))
        for index in range(sector_count)
    ]
    return {
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": vehicle_id,
        "observed_at": _rfc3339(observed_at),
        "max_age_s": max_age_s,
        "validity": "valid",
        "source": f"gz {sensor_id}",
        "payload": {
            "frame": OBSTACLE_FRAME,
            "sensor": {
                "id": sensor_id,
                "min_range_m": scan.range_min_m,
                "max_range_m": scan.range_max_m,
            },
            "sectors": sectors,
        },
    }


def _validate_scan(scan: LaserScan) -> None:
    if len(scan.ranges_m) < 1:
        raise LidarObstacleError("A scan must carry at least one beam.")
    for name, value in (
        ("angle_min_rad", scan.angle_min_rad),
        ("angle_increment_rad", scan.angle_increment_rad),
        ("range_min_m", scan.range_min_m),
        ("range_max_m", scan.range_max_m),
    ):
        if not isfinite(value):
            raise LidarObstacleError(f"Scan field '{name}' must be finite.")
    if scan.angle_increment_rad == 0.0:
        raise LidarObstacleError("Scan beams cannot all share one bearing; the increment is zero.")
    if not 0.0 < scan.range_min_m < scan.range_max_m:
        raise LidarObstacleError("Scan requires 0 < range_min_m < range_max_m.")


def _bin_beams_into_sectors(scan: LaserScan, sector_width_deg: float) -> dict[int, _SectorSamples]:
    sector_count = round(360.0 / sector_width_deg)
    beam_counts: dict[int, int] = {}
    in_range: dict[int, list[float]] = {}
    for beam_index, distance in enumerate(scan.ranges_m):
        bearing_ccw_rad = scan.angle_min_rad + beam_index * scan.angle_increment_rad
        # gz measures counter-clockwise from forward; the obstacle frame's yaw is
        # clockwise from above, so the two directions are opposite.
        yaw_frd_deg = -degrees(bearing_ccw_rad)
        sector = _sector_index(yaw_frd_deg, sector_width_deg, sector_count)
        beam_counts[sector] = beam_counts.get(sector, 0) + 1
        if _is_in_range_return(distance, scan.range_max_m):
            in_range.setdefault(sector, []).append(float(distance))
    return {
        sector: _SectorSamples(beam_counts[sector], tuple(in_range.get(sector, ())))
        for sector in beam_counts
    }


def _sector_index(yaw_frd_deg: float, sector_width_deg: float, sector_count: int) -> int:
    return round(yaw_frd_deg / sector_width_deg) % sector_count


def _is_in_range_return(distance: object, range_max_m: float) -> bool:
    """A finite return strictly inside the sensor's range is a real obstacle.

    ``inf`` means the beam hit nothing, and a value at or past the maximum is the
    sensor saying "clear to the edge", not an obstacle sitting on the edge.
    """
    if type(distance) not in (int, float):
        return False
    value = float(distance)
    if not isfinite(value) or isnan(value):
        return False
    return 0.0 < value < range_max_m - _RANGE_MAX_EPSILON_M


def _sector_document(index: int, sector_width_deg: float, samples: _SectorSamples) -> dict:
    yaw_deg = _sector_centre_deg(index, sector_width_deg)
    sector: dict[str, object] = {"yaw_deg": yaw_deg, "width_deg": sector_width_deg}
    if samples.beam_count == 0:
        # No beam ever pointed here: the sensor cannot speak for this bearing.
        sector["coverage"] = "unobserved"
        return sector
    if not samples.in_range_distances:
        # The sensor swept this bearing and every beam ran to the edge.
        sector["coverage"] = "clear"
        return sector
    sector["coverage"] = "measured"
    sector["distance_m"] = min(samples.in_range_distances)
    sector["confidence"] = _confidence(samples)
    sector["stddev_m"] = round(pstdev(samples.in_range_distances), 6) if len(samples.in_range_distances) > 1 else 0.0
    return sector


def _sector_centre_deg(index: int, sector_width_deg: float) -> float:
    centre = index * sector_width_deg
    if centre > 180.0:
        centre -= 360.0
    # Round-trip through the sensor keeps small binary noise out of the schema.
    return round(centre, 6)


def _confidence(samples: _SectorSamples) -> float:
    """How much of the sector actually returned an obstacle, in [0, 1]."""
    return round(len(samples.in_range_distances) / samples.beam_count, 6)


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
