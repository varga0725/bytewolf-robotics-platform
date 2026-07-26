"""The Offboard control boundary: the only path a streamed setpoint may take.

Separate from ``brain/telemetry`` on purpose. That package is read-only by
design and must stay that way; this one commands. Keeping them apart means the
telemetry bridge cannot quietly grow a control path, which is exactly the drift
the autonomy roadmap warns about.

Separate from ``brain/adapters/mavsdk_adapter.py`` too. That carries the mission
path -- bounded, one-shot, human-approved commands. This carries a continuous
stream. They fail differently and are guarded differently.

Nothing here is active by default: ``safety.offboard.enabled`` in ``twin.yaml``
is false, and a session runs in shadow mode unless both that flag and an
explicit argument say otherwise.
"""

from brain.control.boundary import (
    BoundaryDecision,
    OffboardBoundary,
    RejectionReason,
)
from brain.control.contract import (
    OFFBOARD_CONTRACT_VERSION,
    OffboardContractError,
    OffboardSetpoint,
    SetpointFrame,
    SetpointState,
    Velocity,
    load_setpoint,
)
from brain.control.session import (
    OffboardAdapterError,
    OffboardSession,
    SessionOutcome,
    SessionRecord,
    VelocityAdapter,
)
from brain.control.watchdog import (
    OffboardWatchdog,
    StreamState,
    WatchdogDecision,
)


__all__ = [
    "OFFBOARD_CONTRACT_VERSION",
    "BoundaryDecision",
    "OffboardAdapterError",
    "OffboardBoundary",
    "OffboardContractError",
    "OffboardSession",
    "OffboardSetpoint",
    "OffboardWatchdog",
    "RejectionReason",
    "SessionOutcome",
    "SessionRecord",
    "SetpointFrame",
    "SetpointState",
    "StreamState",
    "VelocityAdapter",
    "Velocity",
    "WatchdogDecision",
]
