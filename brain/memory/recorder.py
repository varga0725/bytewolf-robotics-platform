"""Write world-memory claims from the runs that actually produce evidence.

Until this module existed the world store had converters and a read API but no
writer: every claim in it had to be put there by hand. A memory nothing writes
is a schema, not a memory.

Recording is deliberately subordinate to the run that produced the evidence. A
flight, a scenario, or a scan must not fail because a log line could not be
appended, so a failure here is *reported* rather than raised — the same rule the
Pi post-turn hook follows. Losing a claim costs a memory; raising would cost the
run and, in flight, the audit trail that matters far more.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from brain.memory.evidence import (
    claim_from_mission_artifact,
    claim_from_target_observation,
    claims_from_obstacle_observation,
)
from brain.memory.world_map import MapGrid, VehiclePose, map_claims_from_obstacle_observation
from brain.memory.world_memory import WorldClaim, WorldMemoryError, append_claim
from brain.mission.artifacts import MissionAuditArtifact
from brain.perception.target_estimator import TargetObservation
from brain.telemetry.observation import Observation


DEFAULT_WORLD_MEMORY_PATH = Path("var/world-memory/claims.jsonl")
# One scan can touch many cells; a burst of scans must not turn the log into an
# unbounded write. Anything past this in a single call is dropped and counted.
MAX_CLAIMS_PER_CALL = 200


@dataclass(frozen=True)
class RecordingResult:
    """What a recording attempt actually managed to persist."""

    written: int
    dropped: int = 0
    failure: str | None = None

    @property
    def complete(self) -> bool:
        return self.dropped == 0 and self.failure is None

    def as_dict(self) -> dict[str, object]:
        return {"written": self.written, "dropped": self.dropped, "failure": self.failure}


@dataclass(frozen=True)
class WorldMemoryRecorder:
    """Append evidence to the world log without ever endangering its producer."""

    path: Path = DEFAULT_WORLD_MEMORY_PATH
    max_claims_per_call: int = MAX_CLAIMS_PER_CALL

    def record(self, claims: Iterable[WorldClaim]) -> RecordingResult:
        candidates = list(claims)
        admitted = candidates[: self.max_claims_per_call]
        dropped = len(candidates) - len(admitted)
        written = 0
        for claim in admitted:
            try:
                append_claim(self.path, claim)
            except OSError as error:
                return RecordingResult(written, dropped, f"{type(error).__name__}: {error.strerror}")
            written += 1
        return RecordingResult(written, dropped)

    def record_obstacle_scan(
        self,
        observation: Observation,
        now: datetime,
        *,
        pose: VehiclePose | None = None,
        grid: MapGrid | None = None,
        artifact: str | None = None,
    ) -> RecordingResult:
        """Record what a scan saw, and where it was, when the pose is known.

        Without a pose there is no map: a body-frame sector cannot be placed on
        a grid, so only the sector-level obstacle claims are written. Guessing a
        pose would put a wall at a coordinate nobody measured.
        """
        claims = list(claims_from_obstacle_observation(observation, now, artifact=artifact))
        if pose is not None and grid is not None:
            claims.extend(
                map_claims_from_obstacle_observation(observation, pose, grid, now, artifact=artifact)
            )
        return self.record(claims)

    def record_target_sighting(
        self,
        observation: TargetObservation,
        now: datetime,
        *,
        subject: str,
        artifact: str | None = None,
    ) -> RecordingResult:
        claim = claim_from_target_observation(observation, now, subject=subject, artifact=artifact)
        return self.record([claim] if claim is not None else [])

    def record_mission_outcome(
        self, artifact: MissionAuditArtifact, *, artifact_path: str | None = None
    ) -> RecordingResult:
        """Record a finished run, or report why the run was not recordable.

        A malformed artifact is the one case that can raise out of the
        converters; it is turned into a reported failure here so that writing
        the mission's own audit file is never at risk.
        """
        try:
            claim = claim_from_mission_artifact(artifact, artifact_path=artifact_path)
        except WorldMemoryError as error:
            return RecordingResult(0, 0, f"WorldMemoryError: {error}")
        return self.record([claim])
