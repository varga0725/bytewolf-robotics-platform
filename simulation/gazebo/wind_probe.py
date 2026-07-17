"""Confirm from Gazebo's ground truth that a wind fixture actually reached the vehicle.

A wind report that only names the fixture it handed to the launcher proves the
fixture was built, not that PX4 loaded it: a dropped environment variable falls
back to the still-air default world and the mission passes just the same.

This observes the vehicle instead of trusting the handoff.  To hold station in
wind a multicopter must tilt into it, by an angle the fixture itself predicts:
the wind force is ``base_link_mass * scaling_factor * wind_speed`` and the
weight is ``total_mass * g``, so the steady hover tilt is the arctangent of
their ratio.  At 10 m/s that is ~8 degrees, and in still air it is ~0, so a
fixture that silently failed to load cannot produce a passing observation.

The vehicle pose comes from Gazebo rather than from the flight stack, so the
check stays independent of the software under test, and nothing here touches the
mission or safety path.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import json
from math import acos, atan, degrees, isfinite
from statistics import median
import subprocess
from types import TracebackType


STANDARD_GRAVITY_M_S2 = 9.80665

# Below this altitude the vehicle is on or near the ground, where its tilt says
# nothing about wind: it is held level by the ground, not by the controller.
AIRBORNE_ALTITUDE_M = 1.0

# The tilt band a run must land in to count as feeling the modelled wind. It is
# wide enough for controller transients and the sim's own gravity constant, and
# far too narrow to accept the ~0 degrees of a fixture that never loaded.
TILT_TOLERANCE_FRACTION = 0.4
TILT_TOLERANCE_FLOOR_DEG = 1.0

# A verdict from a handful of samples would be noise, not evidence.
MINIMUM_AIRBORNE_SAMPLES = 5


class WindProbeError(ValueError):
    """Raised when the vehicle's wind response cannot be observed."""


@dataclass(frozen=True)
class TiltObservation:
    """What the vehicle's own attitude says about the wind it flew in."""

    samples: int
    airborne_samples: int
    median_airborne_tilt_deg: float | None
    expected_tilt_deg: float
    matches_expected_wind: bool
    detail: str


def tilt_deg_from_orientation(orientation: dict[str, object]) -> float:
    """Return the angle between the vehicle's up axis and the world's.

    Gazebo omits zero components, so every term defaults to zero.
    """
    x = _finite(orientation.get("x", 0.0), "orientation.x")
    y = _finite(orientation.get("y", 0.0), "orientation.y")
    cosine = 1.0 - 2.0 * (x * x + y * y)
    return degrees(acos(max(-1.0, min(1.0, cosine))))


def expected_hover_tilt_deg(
    wind_speed_m_s: float,
    scaling_factor_per_s: float,
    airframe_mass_kg: float,
    total_mass_kg: float,
    gravity_m_s2: float = STANDARD_GRAVITY_M_S2,
) -> float:
    """Return the tilt the fixture's own wind force implies for steady hover."""
    for name, value in (
        ("wind speed", wind_speed_m_s),
        ("scaling factor", scaling_factor_per_s),
        ("airframe mass", airframe_mass_kg),
        ("total mass", total_mass_kg),
        ("gravity", gravity_m_s2),
    ):
        if not isfinite(value) or value <= 0.0:
            raise WindProbeError(f"Expected hover tilt needs a positive, finite {name}.")
    drag_n = airframe_mass_kg * scaling_factor_per_s * wind_speed_m_s
    weight_n = total_mass_kg * gravity_m_s2
    return degrees(atan(drag_n / weight_n))


def observe_tilt(
    messages: Iterable[str],
    model_name: str,
    expected_tilt_deg: float,
    *,
    airborne_altitude_m: float = AIRBORNE_ALTITUDE_M,
    minimum_airborne_samples: int = MINIMUM_AIRBORNE_SAMPLES,
) -> TiltObservation:
    """Judge a pose stream against the tilt the fixture predicts."""
    samples = 0
    airborne_tilts: list[float] = []
    for pose in _vehicle_poses(messages, model_name):
        samples += 1
        altitude = _finite(pose.get("position", {}).get("z", 0.0), "position.z")
        if altitude >= airborne_altitude_m:
            airborne_tilts.append(tilt_deg_from_orientation(pose.get("orientation", {})))

    if len(airborne_tilts) < minimum_airborne_samples:
        return TiltObservation(
            samples=samples,
            airborne_samples=len(airborne_tilts),
            median_airborne_tilt_deg=None,
            expected_tilt_deg=expected_tilt_deg,
            matches_expected_wind=False,
            detail=(
                f"Only {len(airborne_tilts)} airborne pose samples of {samples}; "
                f"at least {minimum_airborne_samples} are needed to judge the wind response."
            ),
        )

    observed = median(airborne_tilts)
    tolerance = max(TILT_TOLERANCE_FLOOR_DEG, TILT_TOLERANCE_FRACTION * expected_tilt_deg)
    matches = abs(observed - expected_tilt_deg) <= tolerance
    return TiltObservation(
        samples=samples,
        airborne_samples=len(airborne_tilts),
        median_airborne_tilt_deg=observed,
        expected_tilt_deg=expected_tilt_deg,
        matches_expected_wind=matches,
        detail=(
            f"Median airborne tilt {observed:.2f} deg against {expected_tilt_deg:.2f} deg expected "
            f"(tolerance {tolerance:.2f} deg)."
            + ("" if matches else " The vehicle did not fly the modelled wind.")
        ),
    )


def _vehicle_poses(messages: Iterable[str], model_name: str) -> Iterator[dict]:
    for line in messages:
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            # A terminated stream can end mid-line; earlier samples stay valid.
            continue
        if not isinstance(document, dict):
            continue
        for pose in document.get("pose", []):
            if isinstance(pose, dict) and pose.get("name") == model_name:
                yield pose


def _finite(value: object, name: str) -> float:
    if type(value) not in (int, float) or not isfinite(float(value)):
        raise WindProbeError(f"Gazebo pose field '{name}' must be a finite number.")
    return float(value)


class GazeboPoseObserver:
    """Record one world's pose stream for the life of a scenario.

    The raw stream is megabytes of every entity in the world, so it is held in a
    caller-owned file and only the verdict survives into the report.
    """

    def __init__(self, world_name: str, model_name: str, capture_path) -> None:
        self._world_name = world_name
        self._model_name = model_name
        self._capture_path = capture_path
        self._process: subprocess.Popen | None = None
        self._stream = None

    def __enter__(self) -> GazeboPoseObserver:
        self._capture_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._capture_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(
            (
                "gz", "topic", "-e",
                "-t", f"/world/{self._world_name}/pose/info",
                "--json-output",
            ),
            stdout=self._stream,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, exc_type: type | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5.0)
        if self._stream is not None:
            self._stream.close()

    def observation(self, expected_tilt_deg: float) -> TiltObservation:
        with self._capture_path.open("r", encoding="utf-8") as stream:
            return observe_tilt(stream, self._model_name, expected_tilt_deg)
