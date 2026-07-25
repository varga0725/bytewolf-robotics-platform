"""Apply PX4's own fault-injection parameters to a booted SITL, and prove they took.

What PX4 SITL actually supports, probed against PX4 v1.17.0 + gz-sim 8:

* **Battery** — supported in flight.  ``battery_simulator`` runs by default and
  MAVSDK reports a real percentage and voltage.  ``SIM_BAT_DRAIN`` sets the
  seconds for a full 100→0 discharge and ``SIM_BAT_MIN_PCT`` the floor it stops
  at; PX4 only drains while armed and resets to 100% on disarm, so a low battery
  can only be reached in flight, never before arming.
* **GNSS** — boot-time only.  ``SIM_GZ_EN_GPS`` is declared ``reboot_required``,
  so it can remove GPS from a run that starts without it, but cannot drop GNSS
  mid-flight.  An in-flight GNSS fault stays unit/contract evidence.
* **MAVLink/client loss** — not a PX4 parameter at all.  A dead client commands
  nothing, and PX4's own failsafe is the authority; there is nothing here to
  inject and no app-side claim to make.

A parameter is applied through PX4's own client against the running daemon, and
read back.  A write that cannot be confirmed fails closed rather than letting a
scenario claim a fault it never had.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from pathlib import Path
import re
import subprocess
import time


# PX4's parameter client talks to the daemon over its working directory.
PX4_BUILD_DIRECTORY = Path("PX4-Autopilot/build/px4_sitl_default")

# PX4 prefixes each parameter with any of its status symbols -- 'x' used,
# '+' saved, '*' unsaved -- and a freshly set one carries two of them
# ("x * SIM_BAT_DRAIN [837,1415] : 20.0000"), which is exactly the state a
# fault injection reads back.
_PARAM_VALUE = re.compile(r"^\s*(?:[x+*]\s+)*(\S+)\s+\[\d+,\d+\]\s*:\s*(\S+)\s*$", re.MULTILINE)

# PX4 stores parameters as float32, so a read-back is not bit-identical.
_READ_BACK_TOLERANCE = 1e-4
_PARAMETER_CLIENT_TIMEOUT_S = 10.0
_PARAMETER_CLIENT_ATTEMPTS = 3
_PARAMETER_CLIENT_RETRY_DELAY_S = 1.0


class FaultInjectionError(RuntimeError):
    """Raised when a fault cannot be applied or confirmed on the running PX4."""


@dataclass(frozen=True)
class AppliedParameter:
    """One PX4 parameter a run set, and the value PX4 confirmed holding."""

    name: str
    requested: float
    confirmed: float


def apply_px4_parameters(
    parameters: tuple[tuple[str, float], ...],
    *,
    px4_build_directory: Path = PX4_BUILD_DIRECTORY,
    run_command=subprocess.run,
    sleep=time.sleep,
) -> tuple[AppliedParameter, ...]:
    """Set each parameter on the running PX4 and read back what it holds.

    PX4's local parameter client can briefly outlive the daemon's readiness at
    SITL boot.  Retry only that bounded transport timeout; a value that cannot
    be read back still fails closed and never starts the fault scenario.
    """
    return tuple(
        _apply_parameter(name, value, px4_build_directory, run_command, sleep) for name, value in parameters
    )


def _apply_parameter(name: str, value: float, build_directory: Path, run_command, sleep) -> AppliedParameter:
    if not isfinite(value):
        raise FaultInjectionError(f"PX4 parameter '{name}' needs a finite value.")
    _run_param(("set", name, f"{value:g}"), build_directory, run_command, sleep)
    confirmed = _read_parameter(name, build_directory, run_command, sleep)
    if not isclose(confirmed, value, rel_tol=_READ_BACK_TOLERANCE, abs_tol=_READ_BACK_TOLERANCE):
        raise FaultInjectionError(
            f"PX4 parameter '{name}' reads back {confirmed:g} after being set to {value:g}; "
            "the fault cannot be claimed."
        )
    return AppliedParameter(name, value, confirmed)


def _read_parameter(name: str, build_directory: Path, run_command, sleep) -> float:
    output = _run_param(("show", name), build_directory, run_command, sleep)
    for parameter_name, raw_value in _PARAM_VALUE.findall(output):
        if parameter_name == name:
            try:
                return float(raw_value)
            except ValueError as error:
                raise FaultInjectionError(f"PX4 parameter '{name}' returned '{raw_value}'.") from error
    raise FaultInjectionError(f"PX4 did not report a value for parameter '{name}'.")


def _run_param(arguments: tuple[str, ...], build_directory: Path, run_command, sleep) -> str:
    command = (str(Path("bin") / "px4-param"), *arguments)
    for attempt in range(1, _PARAMETER_CLIENT_ATTEMPTS + 1):
        try:
            completed = run_command(
                command,
                cwd=build_directory,
                capture_output=True,
                text=True,
                timeout=_PARAMETER_CLIENT_TIMEOUT_S,
                check=False,
            )
        except OSError as error:
            raise FaultInjectionError(f"Cannot run PX4's parameter client: {error}.") from error
        except subprocess.TimeoutExpired as error:
            if attempt == _PARAMETER_CLIENT_ATTEMPTS:
                raise FaultInjectionError(
                    f"PX4's parameter client timed out on {' '.join(arguments)} after "
                    f"{_PARAMETER_CLIENT_ATTEMPTS} attempts."
                ) from error
            sleep(_PARAMETER_CLIENT_RETRY_DELAY_S)
            continue
        if completed.returncode != 0:
            raise FaultInjectionError(
                f"PX4's parameter client failed on {' '.join(arguments)}: {completed.stderr.strip()}"
            )
        return completed.stdout
    raise AssertionError("The bounded parameter-client retry loop must return or raise.")
