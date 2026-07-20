"""Turn perception and mission evidence into world-memory claims.

Every converter here is pure and fail-closed. An observation that may not be
acted on may not be remembered either: an invalid, missing, or stale reading
produces no claim rather than a low-confidence one, because a remembered guess
outlives the moment that justified it.

Sensor claims expire; a recorded mission outcome does not decay the same way,
but it is still given an explicit horizon, since world memory holds nothing
that never expires.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from brain.memory.world_memory import WorldClaim, WorldMemoryError, load_world_claim
from brain.mission.artifacts import MissionAuditArtifact
from brain.perception.target_estimator import TargetObservation
from brain.telemetry.observation import Observation, ObservationState


DEFAULT_SIGHTING_TTL_S = 300.0
DEFAULT_OBSTACLE_TTL_S = 60.0
DEFAULT_MISSION_TTL_S = 30 * 24 * 3_600.0


def claim_from_target_observation(
    observation: TargetObservation,
    now: datetime,
    *,
    subject: str,
    ttl_s: float = DEFAULT_SIGHTING_TTL_S,
    artifact: str | None = None,
) -> WorldClaim | None:
    """Remember a target sighting only while it is usable evidence."""
    if not observation.state(now).usable or observation.confidence is None:
        return None
    where = ""
    if observation.offset_north_m is not None and observation.offset_east_m is not None:
        where = (
            f" a jármű alatti keret {observation.offset_north_m:+.1f} m észak, "
            f"{observation.offset_east_m:+.1f} m kelet pontján"
        )
    document: dict[str, Any] = {
        "contract_version": "v0.1",
        "subject": subject,
        "category": "target_sighting",
        "statement": f"A(z) '{observation.label or 'ismeretlen'}' célpont látható{where}.",
        "evidence": _evidence(
            source=observation.source or "camera",
            observed_at=observation.captured_at,
            ttl_s=ttl_s,
            confidence=observation.confidence,
            artifact=artifact,
        ),
    }
    if observation.global_fix is not None:
        document["position"] = {
            "frame": "wgs84",
            "latitude_deg": observation.global_fix.latitude_deg,
            "longitude_deg": observation.global_fix.longitude_deg,
        }
    return _claim(document)


def claims_from_obstacle_observation(
    observation: Observation,
    now: datetime,
    *,
    ttl_s: float = DEFAULT_OBSTACLE_TTL_S,
    artifact: str | None = None,
) -> tuple[WorldClaim, ...]:
    """Remember measured obstacle sectors only.

    A `clear` sector is a negative measurement and a `unobserved` sector is no
    measurement at all. Neither becomes a claim: remembering 'nothing there'
    from a sensor that could not see there is how a blind spot turns into free
    space.
    """
    if observation.kind != "obstacle" or observation.state(now) is not ObservationState.VALID:
        return ()
    payload = observation.payload or {}
    sensor = payload.get("sensor", {})
    claims: list[WorldClaim] = []
    for sector in payload.get("sectors", ()):
        if sector.get("coverage") != "measured":
            continue
        yaw = float(sector["yaw_deg"])
        claims.append(_claim({
            "contract_version": "v0.1",
            "subject": f"obstacle:{sensor.get('id', 'sensor')}:{yaw:+.0f}",
            "category": "obstacle",
            "statement": (
                f"Akadály {float(sector['distance_m']):.1f} m-re a jármű {yaw:+.0f}°-os irányában."
            ),
            "evidence": _evidence(
                source=observation.source or f"lidar:{sensor.get('id', 'sensor')}",
                observed_at=observation.observed_at,
                ttl_s=ttl_s,
                confidence=float(sector.get("confidence", 1.0)),
                artifact=artifact,
                vehicle_id=observation.vehicle_id,
            ),
        }))
    return tuple(claims)


def claim_from_mission_artifact(
    artifact: MissionAuditArtifact,
    *,
    artifact_path: str | None = None,
    ttl_s: float = DEFAULT_MISSION_TTL_S,
) -> WorldClaim:
    """Remember what a recorded mission run actually did.

    A run's outcome is a record rather than an estimate, so its confidence is
    full. What it is *not* is permanent: the horizon keeps mission history in
    the same perishable contract as every other claim.
    """
    reason = f" Hibaok: {artifact.failure_reason}." if artifact.failure_reason else ""
    return _claim({
        "contract_version": "v0.1",
        "subject": f"mission:{artifact.run_id}",
        "category": "mission_outcome",
        "statement": (
            f"A küldetés kimenete '{artifact.outcome}', "
            f"{len(artifact.events)} rögzített fázisváltással.{reason}"
        )[:240],
        "evidence": _evidence(
            source="mission-artifact",
            observed_at=artifact.recorded_at,
            ttl_s=ttl_s,
            confidence=1.0,
            artifact=artifact_path,
        ),
    })


def _evidence(
    *,
    source: str,
    observed_at: datetime,
    ttl_s: float,
    confidence: float,
    artifact: str | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    if ttl_s <= 0:
        raise WorldMemoryError("A world claim needs a positive lifetime; a claim cannot expire on arrival.")
    evidence: dict[str, Any] = {
        "source": source,
        "observed_at": observed_at.isoformat(),
        "expires_at": (observed_at + timedelta(seconds=ttl_s)).isoformat(),
        "confidence": confidence,
    }
    if artifact is not None:
        evidence["artifact"] = artifact
    if vehicle_id is not None:
        evidence["vehicle_id"] = vehicle_id
    return evidence


def _claim(document: dict[str, Any]) -> WorldClaim:
    """Route every converter through the same contract check, never around it."""
    return load_world_claim(document)
