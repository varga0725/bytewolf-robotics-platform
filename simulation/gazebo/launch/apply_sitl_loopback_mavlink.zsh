# Bind PX4 SITL MAVLink UDP listeners to loopback only (PX4 PX4_NET_INTERFACE).
# Stock POSIX startup binds all interfaces unless this is set before px4 starts.
export PX4_NET_INTERFACE=lo