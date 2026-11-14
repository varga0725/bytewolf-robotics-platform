"""Fail-closed simulation-only target checks for SITL missions."""

from __future__ import annotations

from urllib.parse import urlparse

from brain.safety.deployment import (
    DeploymentAuthorizationError,
    DeploymentMode,
    authorize_actuation_connection,
    file_sha256,
)
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, SafetyProfile
from brain.mission.demo_errors import DemoPointRtlError


def _normalize_mode(value: str) -> DeploymentMode:
    try:
        return DeploymentMode(value.strip().lower())
    except ValueError as error:
        raise DemoPointRtlError(
            f"Unknown deployment mode '{value}'; only simulation is permitted for SITL demos."
        ) from error


def require_simulation_target(
    *,
    deployment_mode: str,
    endpoint: str,
    profile: SafetyProfile,
    operation: str,
    request_sha256: str,
    safety_profile_sha256: str | None = None,
    permit_path=None,
) -> None:
    """Refuse any non-simulation deployment mode, transport, or physical permit."""
    mode = _normalize_mode(deployment_mode)
    if mode is not DeploymentMode.SIMULATION:
        raise DemoPointRtlError(
            f"Deployment mode '{mode.value}' is not permitted; demo missions require simulation."
        )
    parsed = urlparse(endpoint)
    if parsed.scheme == "serial" or parsed.scheme.startswith("tcp"):
        raise DemoPointRtlError("Demo missions refuse serial and TCP vehicle endpoints.")
    if parsed.scheme not in {"udpin", "udpout"}:
        raise DemoPointRtlError(f"Unsupported MAVSDK endpoint scheme '{parsed.scheme}'.")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        raise DemoPointRtlError("Demo missions require a loopback UDP SITL endpoint.")
    if permit_path is not None:
        raise DemoPointRtlError("A physical actuation permit cannot be supplied for SITL demo missions.")
    try:
        authorize_actuation_connection(
            mode.value,
            endpoint,
            vehicle_id=profile.vehicle_id,
            operation=operation,
            request_sha256=request_sha256,
            safety_profile_sha256=safety_profile_sha256 or file_sha256(DEFAULT_SAFETY_PROFILE_PATH),
            physical_actuation_enabled=profile.physical_actuation_enabled,
            permit_path=None,
        )
    except DeploymentAuthorizationError as error:
        raise DemoPointRtlError(str(error)) from error


def redact_loopback_endpoint(endpoint: str) -> dict[str, object]:
    parsed = urlparse(endpoint)
    port = parsed.port or (14540 if parsed.scheme.startswith("udp") else None)
    return {"transport": "udp-loopback", "port": port}
