"""Loopback MAVLink endpoint defaults for the in-flight acceptance harness."""

from __future__ import annotations

from urllib.parse import urlparse

# PX4 SITL: onboard API is typically 14540; a second onboard instance / GCS path is 14550.
DEFAULT_MAVSDK_ENDPOINT = "udpin://127.0.0.1:14540"
DEFAULT_MAVLINK_ENDPOINT = "udpin://127.0.0.1:14550"


def mavlink_endpoint_for_gcs(*, mavsdk_endpoint: str) -> str:
    """Derive a distinct pymavlink ingest URL that does not share MAVSDK's UDP bind."""
    parsed = urlparse(mavsdk_endpoint)
    port = parsed.port or 14540
    if port == 14540:
        return DEFAULT_MAVLINK_ENDPOINT
    host = parsed.hostname or "127.0.0.1"
    return f"udpin://{host}:{port + 1}"