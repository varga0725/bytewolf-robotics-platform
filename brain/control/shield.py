"""Stop the vehicle flying into something. Geometry, not a dynamics model.

The gate judges a mission command against an envelope. The boundary judges a
stream against timing and limits. Neither of them knows where anything *is*.
This does: it reads obstacle observations and refuses a velocity that would
carry the vehicle into what the sensor can see -- or into what it cannot.

**Deliberately not a control barrier function.** A CBF constrains a dynamics
model, and this twin has no measured dynamics: every physical parameter in
``twin.yaml`` is still ``provenance: simulated``, taken from PX4's generic x500.
A CBF built on those numbers would be a precise statement about a simulator. So
this shield uses distances, sector coverage and a conservative stopping estimate
instead -- coarser, and honest about what it rests on. The CBF is Phase D, and
it needs the bench measurements the missing battery currently blocks.

Three rules do the work:

* **Unobserved is not clear.** A sector the sensor cannot speak for blocks
  motion into it. The 2D lidar sees 270 degrees, so there is a 90-degree blind
  spot behind the vehicle and this is a live constraint, not a theoretical one.
* **Stale is not current.** An observation past its age cannot describe where
  obstacles are now, and an obstacle map that has stopped updating is more
  dangerous than none, because it looks like knowledge.
* **Stopping takes distance.** Clearance is compared against how far the vehicle
  travels before it can stop, not against zero.

What the shield does *not* claim: it says nothing about the vertical axis. A 2D
lidar scans a plane; climbing and descending are unconstrained here, and calling
that "safe" would be a lie of omission. Yaw in place is allowed -- rotating
moves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from math import atan2, cos, degrees, hypot, radians, sin

from brain.control.contract import Velocity
from brain.safety.profile import ShieldLimits
from brain.telemetry.observation import Observation, ObservationState


class ShieldMode(Enum):
    """Whether the shield's verdict actually constrains the vehicle."""

    #: Evaluates and records; the nominal velocity passes through untouched.
    #: This is how the intervention rate and false-stop rate are measured
    #: before anything is allowed to act on them.
    SHADOW = "shadow"
    #: The verdict is applied.
    ACTIVE = "active"


class ShieldVerdict(Enum):
    """Why the shield allowed, constrained or refused a velocity."""

    CLEAR = "clear"
    #: No trustworthy obstacle picture, so no motion at all.
    NO_OBSERVATION = "no_observation"
    STALE_OBSERVATION = "stale_observation"
    #: The vehicle is pointed at a sector the sensor cannot speak for.
    UNOBSERVED_SECTOR = "unobserved_sector"
    #: A measured obstacle is closer than the vehicle can stop in.
    INSUFFICIENT_CLEARANCE = "insufficient_clearance"
    #: The observation is not the shape this shield can reason about.
    UNUSABLE_OBSERVATION = "unusable_observation"

    @property
    def blocks_motion(self) -> bool:
        return self is not ShieldVerdict.CLEAR


@dataclass(frozen=True)
class ShieldDecision:
    """What the shield concluded about one commanded velocity."""

    verdict: ShieldVerdict
    #: What the vehicle should actually be told. In ACTIVE mode a blocking
    #: verdict yields a stop; in SHADOW mode the nominal velocity is returned
    #: unchanged however the verdict came out.
    velocity: Velocity
    detail: str = ""
    #: The clearance that decided it, when a measured obstacle did.
    clearance_m: float | None = None
    #: How far the vehicle would travel before stopping, at this speed.
    stopping_distance_m: float | None = None
    #: The sector that blocked it, by its centre bearing.
    limiting_sector_deg: float | None = None

    @property
    def intervened(self) -> bool:
        """Whether the shield's verdict differed from letting it through."""
        return self.verdict.blocks_motion


#: Only translation is judged. A yaw rate carries the vehicle nowhere, so it is
#: preserved through a stop rather than zeroed with the rest.
def _stopped(velocity: Velocity) -> Velocity:
    return Velocity(0.0, 0.0, 0.0, velocity.yaw_rate_deg_s)


class RuntimeSafetyShield:
    """Refuse velocities that would carry the vehicle into or past what it sees."""

    def __init__(self, limits: ShieldLimits, *, mode: ShieldMode = ShieldMode.SHADOW) -> None:
        if mode is ShieldMode.ACTIVE and not limits.enabled:
            # Two switches, as everywhere else on this path: the twin is the
            # vehicle's policy and the mode is this run's intent.
            raise ValueError("The twin disables the shield, so it cannot run in active mode.")
        self._limits = limits
        self._mode = mode

    @property
    def mode(self) -> ShieldMode:
        return self._mode

    def evaluate(
        self, velocity: Velocity, observation: Observation | None, now: datetime
    ) -> ShieldDecision:
        """Judge one commanded body-frame velocity against the obstacle picture."""
        verdict, detail, clearance, sector = self._judge(velocity, observation, now)
        speed = hypot(velocity.x_m_s, velocity.y_m_s)
        decision_velocity = (
            velocity
            if self._mode is ShieldMode.SHADOW or not verdict.blocks_motion
            else _stopped(velocity)
        )
        return ShieldDecision(
            verdict=verdict,
            velocity=decision_velocity,
            detail=detail,
            clearance_m=clearance,
            stopping_distance_m=self.stopping_distance_m(speed),
            limiting_sector_deg=sector,
        )

    def stopping_distance_m(self, speed_m_s: float) -> float:
        """How far the vehicle travels before it can stop, plus what it needs then.

        Reaction distance at the current speed, the braking distance after that,
        and the standing clearance the twin insists on. Every term is
        conservative and none of them is measured; the figure is a bound to stay
        outside, not a prediction of where the vehicle will end up.
        """
        if speed_m_s <= 0:
            return self._limits.minimum_clearance_m
        reaction_m = speed_m_s * self._limits.reaction_latency_s
        braking_m = (speed_m_s * speed_m_s) / (2.0 * self._limits.braking_deceleration_m_s2)
        return reaction_m + braking_m + self._limits.minimum_clearance_m

    def _judge(
        self, velocity: Velocity, observation: Observation | None, now: datetime
    ) -> tuple[ShieldVerdict, str, float | None, float | None]:
        speed = hypot(velocity.x_m_s, velocity.y_m_s)
        if speed <= 0.0:
            # Not moving horizontally: there is nothing for an obstacle map to
            # forbid, and refusing here would ground a vehicle that only wanted
            # to turn or hold.
            return ShieldVerdict.CLEAR, "No horizontal motion commanded.", None, None

        if observation is None:
            return (
                ShieldVerdict.NO_OBSERVATION,
                "No obstacle observation, so no direction can be shown to be clear.",
                None,
                None,
            )
        state = observation.state(now)
        if state is not ObservationState.VALID:
            verdict = (
                ShieldVerdict.STALE_OBSERVATION
                if state is ObservationState.STALE
                else ShieldVerdict.NO_OBSERVATION
            )
            return verdict, f"The obstacle observation is {state.value}.", None, None
        age_s = (now.astimezone(UTC) - observation.observed_at.astimezone(UTC)).total_seconds()
        if age_s > self._limits.max_observation_age_s:
            # Tighter than the observation's own max_age_s may be: the shield
            # is entitled to demand fresher evidence than a dashboard does.
            return (
                ShieldVerdict.STALE_OBSERVATION,
                f"The obstacle observation is {age_s:.2f} s old, past the shield's "
                f"{self._limits.max_observation_age_s:g} s limit.",
                None,
                None,
            )

        sectors = _sectors_of(observation)
        if sectors is None:
            return (
                ShieldVerdict.UNUSABLE_OBSERVATION,
                "The observation carries no body_frd obstacle sectors.",
                None,
                None,
            )

        heading_deg = degrees(atan2(velocity.y_m_s, velocity.x_m_s))
        required_m = self.stopping_distance_m(speed)
        return self._judge_heading(sectors, heading_deg, required_m)

    def _judge_heading(
        self, sectors: list[dict], heading_deg: float, required_m: float
    ) -> tuple[ShieldVerdict, str, float | None, float | None]:
        """Check every sector the vehicle's path passes through."""
        relevant = [sector for sector in sectors if _covers(sector, heading_deg)]
        if not relevant:
            # A bearing absent from the sector list is unobserved, which the
            # contract states outright: it is never free space.
            return (
                ShieldVerdict.UNOBSERVED_SECTOR,
                f"No sector reports on bearing {heading_deg:.0f} deg, so it is unobserved.",
                None,
                heading_deg,
            )

        for sector in relevant:
            coverage = sector.get("coverage")
            centre = float(sector.get("yaw_deg", heading_deg))
            if coverage == "unobserved" and self._limits.unobserved_is_blocked:
                return (
                    ShieldVerdict.UNOBSERVED_SECTOR,
                    f"The sector at {centre:.0f} deg is unobserved; no coverage means no motion.",
                    None,
                    centre,
                )
            if coverage == "measured":
                distance = sector.get("distance_m")
                if not isinstance(distance, (int, float)):
                    return (
                        ShieldVerdict.UNUSABLE_OBSERVATION,
                        f"The sector at {centre:.0f} deg claims a measurement without a distance.",
                        None,
                        centre,
                    )
                if float(distance) < required_m:
                    return (
                        ShieldVerdict.INSUFFICIENT_CLEARANCE,
                        f"An obstacle {float(distance):.2f} m away at {centre:.0f} deg is inside "
                        f"the {required_m:.2f} m the vehicle needs to stop and stand clear.",
                        float(distance),
                        centre,
                    )
        return ShieldVerdict.CLEAR, "Every sector on this heading is clear.", None, None


def _sectors_of(observation: Observation) -> list[dict] | None:
    """The body_frd sector list, or none when this is not that kind of evidence."""
    payload = observation.payload
    if not isinstance(payload, dict) or payload.get("frame") != "body_frd":
        return None
    sectors = payload.get("sectors")
    if not isinstance(sectors, list):
        return None
    return [sector for sector in sectors if isinstance(sector, dict)]


def _covers(sector: dict, heading_deg: float) -> bool:
    """Whether a sector spans the given bearing, across the -180/180 wrap."""
    centre = sector.get("yaw_deg")
    width = sector.get("width_deg")
    if not isinstance(centre, (int, float)) or not isinstance(width, (int, float)):
        return False
    separation = abs(_wrapped(heading_deg - float(centre)))
    return separation <= float(width) / 2.0


def _wrapped(degrees_value: float) -> float:
    """Fold an angle into [-180, 180], so 179 and -179 are two degrees apart."""
    angle = radians(degrees_value)
    return degrees(atan2(sin(angle), cos(angle)))


__all__ = [
    "RuntimeSafetyShield",
    "ShieldDecision",
    "ShieldMode",
    "ShieldVerdict",
]
