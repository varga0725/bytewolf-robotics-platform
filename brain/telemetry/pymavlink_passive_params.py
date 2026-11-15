"""Read-only MAVLink parameter requests for passive telemetry (PARAM_REQUEST_READ only)."""

from __future__ import annotations

from dataclasses import dataclass, field

from brain.mission.sitl_target import _PARAM_SYS_AUTOSTART
from brain.preflight_params.param_value import decode_mavlink_param_value
from brain.telemetry.vehicle_identity import follow_up_param_reads, vehicle_identity_from_params

# Re-read identity after reconnect or vehicle change; do not treat stale params as proof.
DEFAULT_IDENTITY_REFRESH_S = 30.0


def encode_param_id(name: str) -> bytes:
    encoded = name.encode("utf-8")[:16]
    return encoded + b"\x00" * (16 - len(encoded))


def request_param_read(mavlink: object, *, target_system: int, target_component: int, param_name: str) -> None:
    """Send a single PARAM_REQUEST_READ for one PX4 parameter name."""
    param_id = encode_param_id(param_name)
    send = getattr(getattr(mavlink, "mav", None), "param_request_read_send", None)
    if not callable(send):
        raise RuntimeError("pymavlink connection cannot issue PARAM_REQUEST_READ.")
    send(target_system, target_component, param_id, -1)


@dataclass
class PassiveVehicleIdentityTracker:
    """Collect SYS_AUTOSTART / simulator params; reset when the MAVLink sysid changes."""

    refresh_interval_s: float = DEFAULT_IDENTITY_REFRESH_S
    _sysid: int | None = None
    _sys_autostart: int | None = None
    _sim_gz_en: int | None = None
    _sih_vehicle_type: int | None = None
    _last_request_monotonic: float = field(default=0.0)
    _pending_follow_ups: tuple[str, ...] = ()

    def on_vehicle_sysid(self, sysid: int, *, monotonic: float) -> bool:
        """Return True when identity was cleared because the vehicle changed."""
        if self._sysid is not None and self._sysid != sysid:
            self._reset_identity()
            self._pending_follow_ups = ()
            self._last_request_monotonic = 0.0
            changed = True
        else:
            changed = False
        self._sysid = sysid
        if self._should_request(monotonic):
            self._last_request_monotonic = monotonic
            self._pending_follow_ups = (_PARAM_SYS_AUTOSTART,)
        return changed

    def _should_request(self, monotonic: float) -> bool:
        if self._last_request_monotonic <= 0.0:
            return True
        return (monotonic - self._last_request_monotonic) >= self.refresh_interval_s

    def pop_follow_up_requests(self) -> tuple[str, ...]:
        pending = self._pending_follow_ups
        self._pending_follow_ups = ()
        return pending

    def apply_param_value(self, message: object) -> bool:
        """Decode one PARAM_VALUE; return True when the derived identity block changed."""
        name = str(getattr(message, "param_id", "")).rstrip("\x00")
        if not name:
            return False
        try:
            decoded = decode_mavlink_param_value(float(message.param_value), int(message.param_type))
        except (TypeError, ValueError):
            return False
        if not decoded.is_integer:
            return False
        value = int(decoded.display_value)

        before = self.identity_block()
        if name == _PARAM_SYS_AUTOSTART:
            self._sys_autostart = value
            self._sim_gz_en = None
            self._sih_vehicle_type = None
            self._pending_follow_ups = follow_up_param_reads(value)
        elif name == "SIM_GZ_EN":
            self._sim_gz_en = value
        elif name == "SIH_VEHICLE_TYPE":
            self._sih_vehicle_type = value
        else:
            return False
        after = self.identity_block()
        return before != after

    def identity_block(self) -> dict[str, object] | None:
        return vehicle_identity_from_params(
            sys_autostart=self._sys_autostart,
            sim_gz_en=self._sim_gz_en,
            sih_vehicle_type=self._sih_vehicle_type,
        )

    def _reset_identity(self) -> None:
        self._sys_autostart = None
        self._sim_gz_en = None
        self._sih_vehicle_type = None


__all__ = [
    "DEFAULT_IDENTITY_REFRESH_S",
    "PassiveVehicleIdentityTracker",
    "encode_param_id",
    "request_param_read",
]