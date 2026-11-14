"""Fail-closed deployment boundary for every MAVSDK connection.

The same process can be useful in four very different environments.  Naming
the environment is not enough by itself: the requested capability and the
transport endpoint must agree with it, and physical actuation additionally
needs a short-lived, target-bound, two-person permit.

The shipped X500 twin and an independent software-release lock both keep
physical actuation disabled. A valid permit is therefore necessary but not
sufficient; changing configuration alone cannot release physical flight.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from hashlib import sha256
import os
from pathlib import Path
from stat import S_IWGRP, S_IWOTH
from typing import Any, Mapping
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


_PERMIT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "shared/schemas/safety/physical_actuation_permit_v0_1.schema.json"
)
_PERMIT_VALIDATOR = Draft202012Validator(
    json.loads(_PERMIT_SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)
_MAX_PERMIT_VALIDITY_S = 15 * 60
_DEPLOYMENT_ENVIRONMENT_VARIABLE = "BYTEWOLF_DEPLOYMENT_MODE"
# A reviewed future release must change code as well as configuration. Keeping
# this independent lock closed means an alternate or accidentally edited twin
# cannot turn the dormant permit contract into a physical flight path.
_PHYSICAL_ACTUATION_RELEASED = False


class DeploymentMode(str, Enum):
    """The explicitly selected relationship to a vehicle."""

    SIMULATION = "simulation"
    BENCH_READONLY = "bench_readonly"
    PHYSICAL_TELEMETRY = "physical_telemetry"
    PHYSICAL_ACTUATION = "physical_actuation"

    def __str__(self) -> str:
        """Match StrEnum's value rendering on the deployed Python 3.10."""
        return self.value


class DeploymentAuthorizationError(RuntimeError):
    """Raised before MAVSDK is imported or a vehicle endpoint is opened."""


@dataclass(frozen=True)
class PhysicalActuationPermit:
    """A short-lived authorization bound to one target and one operation."""

    permit_id: str
    vehicle_id: str
    endpoint: str
    operation: str
    request_sha256: str
    safety_profile_sha256: str
    operator: str
    safety_observer: str
    approved_at: datetime
    expires_at: datetime


def add_deployment_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_mode: DeploymentMode = DeploymentMode.SIMULATION,
    actuation_capable: bool,
) -> None:
    """Add the shared, reviewable deployment controls to a CLI parser."""

    configured_default = os.environ.get(_DEPLOYMENT_ENVIRONMENT_VARIABLE, default_mode.value)
    parser.add_argument(
        "--deployment-mode",
        choices=tuple(mode.value for mode in DeploymentMode),
        default=configured_default,
        help=(
            "Explicit execution environment. Defaults to simulation; read-only physical "
            "modes can never authorize flight."
        ),
    )
    if actuation_capable:
        parser.add_argument(
            "--physical-actuation-permit",
            type=Path,
            default=None,
            help=(
                "Short-lived two-person permit bound to the physical vehicle, endpoint, "
                "and exact operation. It is ignored nowhere and is never needed for SITL."
            ),
        )


def authorize_readonly_connection(mode: str | DeploymentMode, endpoint: str) -> DeploymentMode:
    """Authorize a telemetry-only connection and return its normalized mode."""

    selected = _deployment_mode(mode)
    _validate_endpoint(endpoint)
    if selected is DeploymentMode.SIMULATION:
        _require_local_simulation_endpoint(endpoint)
    elif selected is DeploymentMode.BENCH_READONLY and urlparse(endpoint).scheme != "serial":
        raise DeploymentAuthorizationError(
            "Bench read-only mode permits a direct serial device only."
        )
    return selected


def authorize_actuation_connection(
    mode: str | DeploymentMode,
    endpoint: str,
    *,
    vehicle_id: str,
    operation: str,
    request_sha256: str,
    safety_profile_sha256: str,
    physical_actuation_enabled: bool,
    permit_path: Path | None,
    now: datetime | None = None,
) -> DeploymentMode:
    """Refuse unsafe execution before any MAVSDK/PX4 connection is created."""

    selected = authorize_readonly_connection(mode, endpoint)
    if selected is DeploymentMode.SIMULATION:
        if permit_path is not None:
            raise DeploymentAuthorizationError(
                "A physical actuation permit cannot be supplied in simulation mode."
            )
        return selected
    if selected in {DeploymentMode.BENCH_READONLY, DeploymentMode.PHYSICAL_TELEMETRY}:
        raise DeploymentAuthorizationError(
            f"Deployment mode '{selected.value}' is read-only and cannot authorize actuation."
        )
    if not _PHYSICAL_ACTUATION_RELEASED:
        raise DeploymentAuthorizationError(
            "Physical actuation is not released in this software version. Configuration "
            "and a permit cannot override the release lock."
        )
    if not physical_actuation_enabled:
        raise DeploymentAuthorizationError(
            "Physical actuation is disabled by the active vehicle twin. A permit cannot "
            "override that policy."
        )
    if permit_path is None:
        raise DeploymentAuthorizationError(
            "Physical actuation requires --physical-actuation-permit."
        )
    permit = load_physical_actuation_permit(
        permit_path,
        vehicle_id=vehicle_id,
        endpoint=endpoint,
        operation=operation,
        request_sha256=request_sha256,
        safety_profile_sha256=safety_profile_sha256,
        now=now,
    )
    _consume_physical_actuation_permit(permit_path, permit, now=now)
    return selected


def load_physical_actuation_permit(
    path: Path,
    *,
    vehicle_id: str,
    endpoint: str,
    operation: str,
    request_sha256: str,
    safety_profile_sha256: str,
    now: datetime | None = None,
) -> PhysicalActuationPermit:
    """Load and verify a target-bound permit without accepting loose defaults."""

    try:
        mode = path.stat().st_mode
        if mode & (S_IWGRP | S_IWOTH):
            raise DeploymentAuthorizationError(
                f"Physical actuation permit '{path}' is group- or world-writable."
            )
        document = json.loads(path.read_text(encoding="utf-8"))
    except DeploymentAuthorizationError:
        raise
    except OSError as error:
        raise DeploymentAuthorizationError(
            f"Cannot read physical actuation permit '{path}': {error.strerror}."
        ) from error
    except json.JSONDecodeError as error:
        raise DeploymentAuthorizationError(
            f"Physical actuation permit '{path}' is not valid JSON."
        ) from error

    errors = sorted(_PERMIT_VALIDATOR.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(
            f"{'.'.join(str(item) for item in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise DeploymentAuthorizationError(f"Physical actuation permit is invalid: {detail}")
    assert isinstance(document, dict)
    approved_at = _parse_timestamp(document["approved_at"])
    expires_at = _parse_timestamp(document["expires_at"])
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Permit verification time must include a UTC offset.")
    current = current.astimezone(timezone.utc)
    if approved_at > current:
        raise DeploymentAuthorizationError("Physical actuation permit is not active yet.")
    if current >= expires_at:
        raise DeploymentAuthorizationError("Physical actuation permit has expired.")
    validity_s = (expires_at - approved_at).total_seconds()
    if validity_s <= 0 or validity_s > _MAX_PERMIT_VALIDITY_S:
        raise DeploymentAuthorizationError(
            "Physical actuation permit validity must be positive and no longer than 15 minutes."
        )
    if document["operator"].strip().casefold() == document["safety_observer"].strip().casefold():
        raise DeploymentAuthorizationError(
            "Physical actuation requires distinct operator and safety observer identities."
        )
    bindings = {
        "vehicle_id": vehicle_id,
        "endpoint": endpoint,
        "operation": operation,
        "request_sha256": request_sha256,
        "safety_profile_sha256": safety_profile_sha256,
    }
    for field, expected in bindings.items():
        if document[field] != expected:
            raise DeploymentAuthorizationError(
                f"Physical actuation permit {field} does not match this invocation."
            )
    return PhysicalActuationPermit(
        permit_id=document["permit_id"],
        vehicle_id=document["vehicle_id"],
        endpoint=document["endpoint"],
        operation=document["operation"],
        request_sha256=document["request_sha256"],
        safety_profile_sha256=document["safety_profile_sha256"],
        operator=document["operator"],
        safety_observer=document["safety_observer"],
        approved_at=approved_at,
        expires_at=expires_at,
    )


def actuation_request_sha256(document: Mapping[str, Any]) -> str:
    """Hash the exact, normalized request a physical permit is approving."""

    payload = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def file_sha256(path: Path | str) -> str:
    """Hash a reviewed policy file exactly as it exists on disk."""

    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise DeploymentAuthorizationError(
            f"Cannot hash deployment policy '{path}': {error.strerror}."
        ) from error
    return sha256(payload).hexdigest()


def _consume_physical_actuation_permit(
    path: Path,
    permit: PhysicalActuationPermit,
    *,
    now: datetime | None,
) -> None:
    """Atomically mark a permit used before any vehicle connection is opened."""

    consumed_path = path.with_name(f"{path.name}.consumed")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(consumed_path, flags, 0o600)
    except FileExistsError as error:
        raise DeploymentAuthorizationError(
            f"Physical actuation permit '{path}' has already been consumed."
        ) from error
    except OSError as error:
        raise DeploymentAuthorizationError(
            f"Cannot consume physical actuation permit '{path}': {error.strerror}."
        ) from error
    document = {
        "schema_version": "physical_actuation_permit_consumption.v0.1",
        "permit_id": permit.permit_id,
        "request_sha256": permit.request_sha256,
        "consumed_at": current.isoformat().replace("+00:00", "Z"),
    }
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")


def _deployment_mode(value: str | DeploymentMode) -> DeploymentMode:
    try:
        return DeploymentMode(value)
    except ValueError as error:
        choices = ", ".join(mode.value for mode in DeploymentMode)
        raise DeploymentAuthorizationError(
            f"Unknown deployment mode '{value}'. Expected one of: {choices}."
        ) from error


def _validate_endpoint(endpoint: str) -> None:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise DeploymentAuthorizationError("Vehicle endpoint must be a non-empty string.")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"udpin", "udpout", "serial", "tcp"}:
        raise DeploymentAuthorizationError(
            f"Unsupported vehicle endpoint scheme '{parsed.scheme or '<missing>'}'."
        )
    if parsed.scheme == "serial":
        if not parsed.path or parsed.path == "/":
            raise DeploymentAuthorizationError("Serial endpoint must name a device path.")
        return
    if parsed.hostname is None or parsed.port is None:
        raise DeploymentAuthorizationError("Network endpoint must include a host and port.")


def _require_local_simulation_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"udpin", "udpout"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise DeploymentAuthorizationError(
            "Simulation mode only permits a local UDP SITL endpoint; serial and remote "
            "vehicle endpoints require an explicit physical read-only mode."
        )


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise DeploymentAuthorizationError("Permit timestamps must be RFC 3339 values.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeploymentAuthorizationError("Permit timestamps must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "DeploymentAuthorizationError",
    "DeploymentMode",
    "PhysicalActuationPermit",
    "actuation_request_sha256",
    "add_deployment_arguments",
    "authorize_actuation_connection",
    "authorize_readonly_connection",
    "file_sha256",
    "load_physical_actuation_permit",
]
