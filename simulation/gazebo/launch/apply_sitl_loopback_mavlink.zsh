# PX4 v1.17.0 SITL still binds MAVLink UDP to INADDR_ANY; POSIX px4-rc.mavlink does
# not honour PX4_NET_INTERFACE (that wiring landed after v1.17.0). Do not export a
# false sense of loopback-only binding here — host firewall containment is the
# follow-up documented in docs/handover-2026-07-27.md.
true