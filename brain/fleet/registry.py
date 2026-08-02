"""Hash-bound, local fleet inventory for physical robots and their components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import UUID


FLEET_REGISTRY_VERSION = "fleet_registry.v0.1"


class FleetRegistryError(ValueError):
    """Raised when a physical inventory record cannot be trusted."""


@dataclass(frozen=True)
class FleetObservation:
    observed_at: str
    artifact: str
    evidence_sha256: str
    firmware: dict[str, str | None]


@dataclass(frozen=True)
class FleetRobot:
    platform_id: str
    display_name: str
    kind: str
    twin_id: str | None
    hardware_uid: str
    legacy_uid: str | None
    observations: tuple[FleetObservation, ...]


@dataclass(frozen=True)
class FleetRegistry:
    robots: tuple[FleetRobot, ...] = ()

    def to_document(self) -> dict[str, object]:
        return {
            "version": FLEET_REGISTRY_VERSION,
            "robots": [
                {
                    "platform_id": robot.platform_id,
                    "display_name": robot.display_name,
                    "kind": robot.kind,
                    "twin_id": robot.twin_id,
                    "hardware_uid": robot.hardware_uid,
                    "legacy_uid": robot.legacy_uid,
                    "observations": [
                        {
                            "observed_at": observation.observed_at,
                            "artifact": observation.artifact,
                            "evidence_sha256": observation.evidence_sha256,
                            "firmware": observation.firmware,
                        }
                        for observation in robot.observations
                    ],
                }
                for robot in self.robots
            ],
        }


def load_registry(path: Path) -> FleetRegistry:
    if not path.exists():
        return FleetRegistry()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FleetRegistryError(f"Cannot read fleet registry '{path}': {error}") from error
    if not isinstance(document, dict) or document.get("version") != FLEET_REGISTRY_VERSION:
        raise FleetRegistryError("Fleet registry has an unsupported version.")
    robots = tuple(_load_robot(item) for item in document.get("robots", ()))
    _ensure_unique(robots)
    return FleetRegistry(robots)


def record_usb_bench_observation(
    registry_path: Path,
    artifact_path: Path,
    *,
    artifact_root: Path,
    platform_id: str,
    display_name: str,
    kind: str,
    twin_id: str | None = None,
) -> FleetRegistry:
    """Register one physical robot or append one verified USB bench observation."""
    _validate_uuid(platform_id)
    if not display_name.strip() or not kind.strip():
        raise FleetRegistryError("Fleet display name and robot kind are required.")
    resolved_root = artifact_root.resolve()
    resolved_artifact = artifact_path.resolve()
    if resolved_root not in resolved_artifact.parents or not resolved_artifact.is_file():
        raise FleetRegistryError("Bench artifact must be a regular file inside the configured artifact root.")
    try:
        raw = resolved_artifact.read_bytes()
        artifact = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise FleetRegistryError(f"Bench artifact cannot be read: {error}") from error
    identity = _identity_from_artifact(artifact)
    hardware_uid = identity["hardware_uid"]
    registry = load_registry(registry_path)
    collisions = [robot for robot in registry.robots if robot.hardware_uid == hardware_uid and robot.platform_id != platform_id]
    if collisions:
        raise FleetRegistryError(f"PX4 hardware UID already belongs to '{collisions[0].platform_id}'.")
    relative_artifact = resolved_artifact.relative_to(resolved_root).as_posix()
    observation = FleetObservation(
        observed_at=str(artifact["captured_at"]), artifact=relative_artifact,
        evidence_sha256=hashlib.sha256(raw).hexdigest(), firmware=identity["firmware"],
    )
    existing = next((robot for robot in registry.robots if robot.platform_id == platform_id), None)
    if existing is None:
        robot = FleetRobot(platform_id, display_name.strip(), kind.strip(), twin_id, hardware_uid, identity["legacy_uid"], (observation,))
        updated = FleetRegistry((*registry.robots, robot))
    else:
        if existing.hardware_uid != hardware_uid:
            raise FleetRegistryError("A platform ID cannot be reassigned to a different PX4 hardware UID.")
        if any(item.evidence_sha256 == observation.evidence_sha256 for item in existing.observations):
            return registry
        replacement = FleetRobot(existing.platform_id, existing.display_name, existing.kind, existing.twin_id, existing.hardware_uid, existing.legacy_uid, (*existing.observations, observation))
        updated = FleetRegistry(tuple(replacement if robot.platform_id == platform_id else robot for robot in registry.robots))
    _write_registry(registry_path, updated)
    return updated


def _identity_from_artifact(artifact: object) -> dict[str, object]:
    if not isinstance(artifact, dict) or artifact.get("version") != "usb_bench_diagnostics.v1" or artifact.get("outcome") != "completed":
        raise FleetRegistryError("Fleet registration requires a completed usb_bench_diagnostics.v1 artifact.")
    identity = artifact.get("px4_identity")
    if not isinstance(identity, dict) or identity.get("status") != "observed":
        raise FleetRegistryError("Bench artifact has no observed PX4 identity.")
    hardware_uid = identity.get("hardware_uid")
    if not isinstance(hardware_uid, str) or not hardware_uid.strip() or set(hardware_uid.strip()) == {"0"}:
        raise FleetRegistryError("PX4 hardware UID must be a non-zero observed uid2 value.")
    firmware = identity.get("firmware")
    if not isinstance(firmware, dict):
        firmware = {}
    return {
        "hardware_uid": hardware_uid.strip(),
        "legacy_uid": str(identity["legacy_uid"]) if identity.get("legacy_uid") is not None else None,
        "firmware": {key: str(firmware[key]) if firmware.get(key) is not None else None for key in ("version", "git_hash", "release_type")},
    }


def _load_robot(value: object) -> FleetRobot:
    if not isinstance(value, dict):
        raise FleetRegistryError("Fleet robot record must be an object.")
    _validate_uuid(str(value.get("platform_id", "")))
    uid = value.get("hardware_uid")
    if not isinstance(uid, str) or not uid or set(uid) == {"0"}:
        raise FleetRegistryError("Fleet robot has an invalid hardware UID.")
    observations = tuple(
        FleetObservation(str(item["observed_at"]), str(item["artifact"]), str(item["evidence_sha256"]), dict(item.get("firmware", {})))
        for item in value.get("observations", ()) if isinstance(item, dict)
    )
    return FleetRobot(str(value["platform_id"]), str(value.get("display_name", "")), str(value.get("kind", "")), value.get("twin_id"), uid, value.get("legacy_uid"), observations)


def _ensure_unique(robots: tuple[FleetRobot, ...]) -> None:
    if len({robot.platform_id for robot in robots}) != len(robots) or len({robot.hardware_uid for robot in robots}) != len(robots):
        raise FleetRegistryError("Fleet registry has duplicate platform or hardware identities.")


def _validate_uuid(value: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError) as error:
        raise FleetRegistryError("Fleet platform_id must be a UUID.") from error


def _write_registry(path: Path, registry: FleetRegistry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(registry.to_document(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".pending-", delete=False) as handle:
        temporary_path = Path(handle.name)
        handle.write(f"{payload}\n")
    temporary_path.replace(path)
