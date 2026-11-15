"""PX4 autopilot heartbeat lock and passive vehicle_identity collection."""

from __future__ import annotations

from dataclasses import dataclass, field

from brain.mission.sitl_target import _PARAM_SYS_AUTOSTART
from brain.preflight_params.param_value import decode_mavlink_param_value
from brain.telemetry.vehicle_identity import follow_up_param_reads, vehicle_identity_from_params

_MAV_COMP_ID_AUTOPILOT1 = 1
_MAV_TYPE_GCS = 6
_DEFAULT_LINK_LOSS_S = 3.0
_DEFAULT_IDENTITY_REFRESH_S = 30.0
# Ignore single-sample boot-time glitches; PX4 reboot rewinds time_boot_ms sharply.
_BOOT_TIME_REWIND_TOLERANCE_MS = 1000


def qualifying_autopilot_heartbeat(message: object) -> tuple[int, int] | None:
    """Return (sysid, compid) for heartbeats that may lock passive param reads."""
    try:
        from pymavlink import mavutil
    except ModuleNotFoundError:
        return None

    vehicle_type = int(message.type)
    if vehicle_type == _MAV_TYPE_GCS:
        return None
    autopilot_id = int(message.autopilot)
    if autopilot_id == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
        return None
    if autopilot_id != mavutil.mavlink.MAV_AUTOPILOT_PX4:
        return None
    component = int(getattr(message, "get_srcComponent", lambda: 0)())
    if component not in (0, _MAV_COMP_ID_AUTOPILOT1):
        return None
    sysid = int(getattr(message, "get_srcSystem", lambda: 0)())
    if sysid <= 0 or sysid > 255:
        return None
    compid = _MAV_COMP_ID_AUTOPILOT1 if component == 0 else component
    return sysid, compid


def param_value_from_autopilot(message: object, *, target_sysid: int, target_compid: int) -> str | None:
    """Parameter name when the PARAM_VALUE source matches the locked autopilot."""
    src_sys = int(getattr(message, "get_srcSystem", lambda: 0)())
    src_comp = int(getattr(message, "get_srcComponent", lambda: 0)())
    if src_sys != target_sysid or src_comp != target_compid:
        return None
    name = str(getattr(message, "param_id", "")).rstrip("\x00")
    return name or None


@dataclass
class AutopilotIdentityCollector:
    """Passive SYS_AUTOSTART / simulator params for one locked MAVLink autopilot."""

    link_loss_timeout_s: float = _DEFAULT_LINK_LOSS_S
    refresh_interval_s: float = _DEFAULT_IDENTITY_REFRESH_S
    _target_sysid: int | None = None
    _target_compid: int | None = None
    _last_heartbeat_monotonic: float | None = None
    _last_request_monotonic: float = 0.0
    _pending_follow_ups: tuple[str, ...] = ()
    _sys_autostart: int | None = None
    _sim_gz_en: int | None = None
    _sih_vehicle_type: int | None = None
    _identity_stale: bool = True
    _link_was_lost: bool = False
    _last_time_boot_ms: int | None = field(default=None, repr=False)

    @property
    def target(self) -> tuple[int, int] | None:
        if self._target_sysid is None or self._target_compid is None:
            return None
        return self._target_sysid, self._target_compid

    def observe_autopilot_heartbeat(self, sysid: int, compid: int, *, monotonic: float) -> bool:
        """Lock or refresh the autopilot target. Return True when identity was cleared."""
        cleared = False
        if self._target_sysid is None:
            self._target_sysid = sysid
            self._target_compid = compid
            self._clear_param_proof()
            cleared = True
        elif (self._target_sysid, self._target_compid) != (sysid, compid):
            self._target_sysid = sysid
            self._target_compid = compid
            self._clear_param_proof()
            cleared = True
        elif self._link_was_lost:
            self._clear_param_proof()
            cleared = True

        self._link_was_lost = False
        self._last_heartbeat_monotonic = monotonic
        if self._should_request(monotonic):
            self._last_request_monotonic = monotonic
            self._pending_follow_ups = (_PARAM_SYS_AUTOSTART,)
        return cleared

    def observe_system_time(self, time_boot_ms: int) -> bool:
        """Return True when PX4 appears to have rebooted (time_boot_ms rewound)."""
        if self._last_time_boot_ms is not None and time_boot_ms + _BOOT_TIME_REWIND_TOLERANCE_MS < self._last_time_boot_ms:
            self._clear_param_proof()
            if self._target_sysid is not None:
                self._last_request_monotonic = 0.0
                self._pending_follow_ups = (_PARAM_SYS_AUTOSTART,)
            self._last_time_boot_ms = time_boot_ms
            return True
        self._last_time_boot_ms = time_boot_ms
        return False

    def observe_link_quiet(self, *, monotonic: float) -> bool:
        """Return True when the autopilot heartbeat has been absent long enough to drop identity."""
        if self._last_heartbeat_monotonic is None:
            return False
        if monotonic - self._last_heartbeat_monotonic < self.link_loss_timeout_s:
            return False
        if self._target_sysid is None:
            return False
        self._link_was_lost = True
        self._clear_param_proof()
        self._last_request_monotonic = 0.0
        return True

    def pop_follow_up_requests(self) -> tuple[str, ...]:
        pending = self._pending_follow_ups
        self._pending_follow_ups = ()
        return pending

    def apply_param_value(self, message: object) -> bool:
        target = self.target
        if target is None:
            return False
        name = param_value_from_autopilot(
            message, target_sysid=target[0], target_compid=target[1]
        )
        if name is None:
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
            self._identity_stale = True
        elif name == "SIM_GZ_EN":
            self._sim_gz_en = value
        elif name == "SIH_VEHICLE_TYPE":
            self._sih_vehicle_type = value
        else:
            return False
        self._refresh_stale_flag()
        after = self.identity_block()
        return before != after

    def identity_block(self) -> dict[str, object] | None:
        target = self.target
        if target is None:
            return None
        sysid, compid = target
        if self._identity_stale and self._sys_autostart is None:
            return {
                "stale": True,
                "mavlink_sysid": sysid,
                "mavlink_compid": compid,
            }
        proof = vehicle_identity_from_params(
            sys_autostart=self._sys_autostart,
            sim_gz_en=self._sim_gz_en,
            sih_vehicle_type=self._sih_vehicle_type,
        )
        if proof is None:
            return {
                "stale": True,
                "mavlink_sysid": sysid,
                "mavlink_compid": compid,
            }
        proof["mavlink_sysid"] = sysid
        proof["mavlink_compid"] = compid
        if self._identity_stale:
            proof["stale"] = True
        return proof

    def _refresh_stale_flag(self) -> None:
        if self._sys_autostart is None:
            self._identity_stale = True
            return
        needed = follow_up_param_reads(self._sys_autostart)
        if not needed:
            self._identity_stale = False
            return
        if needed == ("SIM_GZ_EN",):
            self._identity_stale = self._sim_gz_en is None
        elif needed == ("SIH_VEHICLE_TYPE",):
            self._identity_stale = self._sih_vehicle_type is None

    def _clear_param_proof(self) -> None:
        self._sys_autostart = None
        self._sim_gz_en = None
        self._sih_vehicle_type = None
        self._identity_stale = True
        self._pending_follow_ups = ()

    def maybe_schedule_refresh(self, *, monotonic: float) -> None:
        """Re-request parameters on a timer without treating it as a live heartbeat."""
        if self.target is None:
            return
        if not self._should_request(monotonic):
            return
        self._last_request_monotonic = monotonic
        if self._sys_autostart is None:
            self._pending_follow_ups = (_PARAM_SYS_AUTOSTART,)
        else:
            self._pending_follow_ups = follow_up_param_reads(self._sys_autostart)

    def _should_request(self, monotonic: float) -> bool:
        if self._last_request_monotonic <= 0.0:
            return True
        return (monotonic - self._last_request_monotonic) >= self.refresh_interval_s


__all__ = [
    "AutopilotIdentityCollector",
    "param_value_from_autopilot",
    "qualifying_autopilot_heartbeat",
]