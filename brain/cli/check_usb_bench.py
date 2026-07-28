"""Record read-only diagnostics from a USB-powered PX4 flight controller.

This command is deliberately separate from mission preflight: an unplugged
flight battery is useful bench evidence, but never permission to fly.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
import tempfile
from uuid import uuid4

from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server


DEFAULT_ARTIFACT_DIRECTORY = Path("simulation/artifacts/hardware-bench")


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.") from error
    if not isfinite(seconds) or seconds <= 0.0:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.")
    return seconds


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read USB-powered PX4 bench telemetry. It never authorizes or commands flight.",
    )
    parser.add_argument("--endpoint", default="serial:///dev/cu.usbmodem01:57600")
    parser.add_argument("--connection-timeout", type=_positive_seconds, default=30.0)
    parser.add_argument("--sample-timeout", type=_positive_seconds, default=3.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIRECTORY)
    return parser.parse_args(arguments)


async def _sample(stream: AsyncIterator[object], timeout_s: float) -> object | None:
    try:
        return await asyncio.wait_for(anext(stream), timeout=timeout_s)
    except (TimeoutError, StopAsyncIteration):
        return None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _observation(value: object | None) -> dict[str, object]:
    return {"status": "observed", "value": value} if value is not None else {"status": "unavailable"}


def _battery_observation(sample: object | None) -> dict[str, object]:
    if sample is None:
        return {"status": "unavailable", "remaining_percent": None}
    percent = _finite_number(getattr(sample, "remaining_percent", None))
    if percent is None or not 0.0 <= percent <= 100.0:
        return {"status": "invalid", "remaining_percent": None}
    return {"status": "observed", "remaining_percent": percent}


def _health_observation(sample: object | None) -> dict[str, object]:
    if sample is None:
        return {"status": "unavailable"}
    fields = ("is_global_position_ok", "is_home_position_ok", "is_armable")
    if any(not isinstance(getattr(sample, field, None), bool) for field in fields):
        return {"status": "invalid"}
    return {"status": "observed", **{field: getattr(sample, field) for field in fields}}


def _position_observation(sample: object | None) -> dict[str, object]:
    if sample is None:
        return {"status": "unavailable"}
    fields = ("latitude_deg", "longitude_deg", "absolute_altitude_m", "relative_altitude_m")
    values = {field: _finite_number(getattr(sample, field, None)) for field in fields}
    if any(value is None for value in values.values()):
        return {"status": "invalid"}
    return {"status": "observed", **values}


def _gps_observation(sample: object | None) -> dict[str, object]:
    if sample is None:
        return {"status": "unavailable"}
    satellites = getattr(sample, "num_satellites", None)
    if not isinstance(satellites, int) or isinstance(satellites, bool) or satellites < 0:
        return {"status": "invalid"}
    fix_type = getattr(sample, "fix_type", None)
    return {"status": "observed", "num_satellites": satellites, "fix_type": str(fix_type)}


def _flight_readiness(checks: dict[str, dict[str, object]]) -> dict[str, object]:
    reasons: list[str] = []
    health = checks["health"]
    if health["status"] != "observed" or not health["is_global_position_ok"] or not health["is_home_position_ok"]:
        reasons.append("navigation or home telemetry is not ready")
    if checks["battery"]["status"] != "observed":
        reasons.append("battery telemetry is unavailable or invalid")
    if checks["armed"].get("value") is not False or checks["in_air"].get("value") is not False:
        reasons.append("vehicle state is not confirmed disarmed and landed")
    return {"status": "blocked", "reasons": reasons}


def _write_artifact(directory: Path, document: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"usb-bench-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}.json"
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".pending-", delete=False) as handle:
        temporary_path = Path(handle.name)
        handle.write(f"{payload}\n")
    temporary_path.replace(destination)
    return destination


async def run(arguments: argparse.Namespace) -> Path:
    """Connect, take bounded observation samples, and persist evidence."""
    system = None
    document: dict[str, object] = {
        "version": "usb_bench_diagnostics.v1",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "endpoint": arguments.endpoint,
        "outcome": "failed",
        "failure_reason": None,
        "checks": {},
        "flight_readiness": {"status": "blocked", "reasons": ["diagnostics have not completed"]},
    }
    try:
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:  # pragma: no cover - environment guard
            raise RuntimeError("MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt") from error
        system = System(port=arguments.mavsdk_server_port)
        acquire_px4_link("usb-bench-check")
        print(f"Reading PX4 USB bench telemetry at {arguments.endpoint}...")
        await asyncio.wait_for(system.connect(system_address=arguments.endpoint), timeout=arguments.connection_timeout)
        connected = await _sample(system.core.connection_state(), arguments.sample_timeout)
        if connected is None or not bool(getattr(connected, "is_connected", False)):
            raise RuntimeError("PX4 did not report a connected MAVLink state.")
        telemetry = system.telemetry
        health, position, gps, battery, armed, in_air = await asyncio.gather(
            _sample(telemetry.health(), arguments.sample_timeout),
            _sample(telemetry.position(), arguments.sample_timeout),
            _sample(telemetry.gps_info(), arguments.sample_timeout),
            _sample(telemetry.battery(), arguments.sample_timeout),
            _sample(telemetry.armed(), arguments.sample_timeout),
            _sample(telemetry.in_air(), arguments.sample_timeout),
        )
        checks = {
            "connection": {"status": "observed", "value": True},
            "health": _health_observation(health),
            "position": _position_observation(position),
            "gps": _gps_observation(gps),
            "battery": _battery_observation(battery),
            "armed": _observation(armed if isinstance(armed, bool) else None),
            "in_air": _observation(in_air if isinstance(in_air, bool) else None),
        }
        document["checks"] = checks
        document["flight_readiness"] = _flight_readiness(checks)
        document["outcome"] = "completed"
    except Exception as error:
        document["failure_reason"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        artifact_path = _write_artifact(arguments.artifact_dir, document)
        stop_owned_mavsdk_server(system)
    print(f"USB bench evidence written to {artifact_path}")
    return artifact_path


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
