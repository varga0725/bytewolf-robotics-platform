"""Project obstacle sectors onto a fixed local grid of world-memory claims.

An obstacle observation is measured in the vehicle's own frame: sector bearings
are relative to where the nose happened to point. That is useless as memory —
the same wall lands somewhere new every time the vehicle turns. This module
anchors those readings to a grid fixed at a chosen origin, so a second pass
over the same wall reinforces the same cell instead of inventing a new one.

Two honesty constraints shape the design:

* **A sector is a wedge, not a point.** Its distance is the nearest return
  anywhere across `width_deg`, so the lateral position is only known to about
  `distance * width`. Cells are therefore coarse, and the claim says which
  sector width produced it rather than pretending to a point measurement.
* **Only occupancy is remembered.** A `clear` sector is a negative measurement
  and an `unobserved` sector is no measurement; neither writes a cell. This
  grid can say "something was here", never "nothing is here".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, degrees, radians, sin
import re
from typing import Any

from brain.memory.world_memory import WorldClaim, load_world_claim
from brain.telemetry.observation import Observation, ObservationState


DEFAULT_CELL_SIZE_M = 2.0
DEFAULT_MAP_TTL_S = 900.0
_EARTH_RADIUS_M = 6_371_000.0
# `map_region:<cell>m:n<north>:e<east>` — the subject carries its own grid size
# so a stored cell stays readable after the default changes.
_MAP_SUBJECT = re.compile(r"^map_region:(?P<cell>[0-9]+(?:\.[0-9]+)?)m:n(?P<north>-?\d+):e(?P<east>-?\d+)$")


class WorldMapError(ValueError):
    """A pose or grid cannot place an observation on the map."""


@dataclass(frozen=True)
class VehiclePose:
    """Where the vehicle was, and which way it faced, when it measured."""

    latitude_deg: float
    longitude_deg: float
    yaw_deg: float
    """Heading of the vehicle's forward axis: zero is north, positive clockwise."""

    north_m: float = 0.0
    east_m: float = 0.0
    """Offset from the grid origin. Both zero means the vehicle is at the origin."""


@dataclass(frozen=True)
class MapGrid:
    """A fixed local grid anchored at one global origin, usually home."""

    origin_latitude_deg: float
    origin_longitude_deg: float
    cell_size_m: float = DEFAULT_CELL_SIZE_M

    def __post_init__(self) -> None:
        if not self.cell_size_m > 0:
            raise WorldMapError("A map grid needs a positive cell size.")

    def cell_of(self, north_m: float, east_m: float) -> tuple[int, int]:
        return (int(north_m // self.cell_size_m), int(east_m // self.cell_size_m))

    def centre_of(self, cell: tuple[int, int]) -> tuple[float, float]:
        half = self.cell_size_m / 2
        return (cell[0] * self.cell_size_m + half, cell[1] * self.cell_size_m + half)

    def global_of(self, cell: tuple[int, int]) -> tuple[float, float]:
        north_m, east_m = self.centre_of(cell)
        latitude = self.origin_latitude_deg + degrees(north_m / _EARTH_RADIUS_M)
        longitude = self.origin_longitude_deg + degrees(
            east_m / (_EARTH_RADIUS_M * cos(radians(self.origin_latitude_deg)))
        )
        return (latitude, longitude)

    def subject_of(self, cell: tuple[int, int]) -> str:
        return f"map_region:{self.cell_size_m:g}m:n{cell[0]}:e{cell[1]}"


@dataclass(frozen=True)
class MapCell:
    """One occupied cell as the dashboard reads it back."""

    north_m: float
    east_m: float
    cell_size_m: float
    confidence: float
    observed_at: datetime
    source: str
    disputed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "north_m": self.north_m,
            "east_m": self.east_m,
            "cell_size_m": self.cell_size_m,
            "confidence": self.confidence,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
            "disputed": self.disputed,
        }


def map_claims_from_obstacle_observation(
    observation: Observation,
    pose: VehiclePose,
    grid: MapGrid,
    now: datetime,
    *,
    ttl_s: float = DEFAULT_MAP_TTL_S,
    artifact: str | None = None,
) -> tuple[WorldClaim, ...]:
    """Turn the measured sectors of one scan into occupancy claims.

    Sectors landing in the same cell collapse to the most confident one: two
    beams hitting one wall are one piece of evidence about that wall, not two.
    """
    if observation.kind != "obstacle" or observation.state(now) is not ObservationState.VALID:
        return ()
    if ttl_s <= 0:
        raise WorldMapError("A map claim needs a positive lifetime.")
    payload = observation.payload or {}
    sensor = payload.get("sensor", {})
    strongest: dict[tuple[int, int], tuple[float, float, float]] = {}
    for sector in payload.get("sectors", ()):
        if sector.get("coverage") != "measured":
            continue
        distance_m = float(sector["distance_m"])
        bearing_deg = pose.yaw_deg + float(sector["yaw_deg"])
        north_m = pose.north_m + distance_m * cos(radians(bearing_deg))
        east_m = pose.east_m + distance_m * sin(radians(bearing_deg))
        confidence = float(sector.get("confidence", 1.0))
        cell = grid.cell_of(north_m, east_m)
        held = strongest.get(cell)
        if held is None or confidence > held[0]:
            strongest[cell] = (confidence, distance_m, float(sector["width_deg"]))
    claims: list[WorldClaim] = []
    for cell, (confidence, distance_m, width_deg) in sorted(strongest.items()):
        latitude, longitude = grid.global_of(cell)
        north_centre, east_centre = grid.centre_of(cell)
        claims.append(load_world_claim({
            "contract_version": "v0.1",
            "subject": grid.subject_of(cell),
            "category": "map_region",
            "statement": (
                f"Akadály a rácscellában (É {north_centre:+.0f} m, K {east_centre:+.0f} m); "
                f"{distance_m:.1f} m-re mérve egy {width_deg:.0f}°-os szektorból."
            )[:240],
            "evidence": {
                "source": observation.source or f"lidar:{sensor.get('id', 'sensor')}",
                "observed_at": observation.observed_at.isoformat(),
                "expires_at": (observation.observed_at + timedelta(seconds=ttl_s)).isoformat(),
                "confidence": confidence,
                **({"vehicle_id": observation.vehicle_id} if observation.vehicle_id else {}),
                **({"artifact": artifact} if artifact else {}),
            },
            "position": {"frame": "wgs84", "latitude_deg": latitude, "longitude_deg": longitude},
        }))
    return tuple(claims)


def map_cell_of_claim(claim: WorldClaim, *, disputed: bool = False) -> MapCell | None:
    """Read a stored claim back as a grid cell, or refuse it.

    A `map_region` claim whose subject does not describe a grid cell is not
    readable as a map: it is skipped rather than guessed at.
    """
    if claim.category != "map_region":
        return None
    match = _MAP_SUBJECT.match(claim.subject)
    if match is None:
        return None
    cell_size_m = float(match.group("cell"))
    half = cell_size_m / 2
    return MapCell(
        north_m=int(match.group("north")) * cell_size_m + half,
        east_m=int(match.group("east")) * cell_size_m + half,
        cell_size_m=cell_size_m,
        confidence=claim.confidence,
        observed_at=claim.observed_at,
        source=claim.source,
        disputed=disputed,
    )


def map_view(claims: tuple[WorldClaim, ...], disputed: tuple[WorldClaim, ...] = ()) -> list[MapCell]:
    """Project the currently believed claims into drawable cells.

    Disputed cells travel with the view but carry their flag: the map shows
    where the evidence disagrees rather than quietly dropping the conflict.
    """
    cells = [cell for claim in claims if (cell := map_cell_of_claim(claim)) is not None]
    cells.extend(
        cell for claim in disputed if (cell := map_cell_of_claim(claim, disputed=True)) is not None
    )
    return cells
