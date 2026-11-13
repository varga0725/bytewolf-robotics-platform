"""Shared errors for the Monday demo-point-rtl mission path."""


class DemoPointRtlError(RuntimeError):
    """Raised when the demo mission cannot be offered or executed for the active target."""


class DemoWaypointMissionError(RuntimeError):
    """Raised when the multi-waypoint demo cannot be offered or executed for the active target."""
