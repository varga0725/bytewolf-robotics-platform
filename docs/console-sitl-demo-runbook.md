# Console SITL demo runbook

This page will collect the operator flow for map-picked multi-waypoint missions
spawned from the Ground-mode Control Room console. Full steps are tracked in
Linear BYT-84; this section documents the optional Gazebo follow camera once
BYT-86 is on `develop`.

## Gazebo follow camera (console-spawned demo-waypoint only)

The API spawns `brain.cli.fly_demo_waypoint_mission` with `--deployment-mode
simulation` and inherits the console unit environment. When
`BYTEWOLF_SITL_ACCEPTANCE` is **not** set, the mission can apply a follow or
overview camera through `DemoGzVisualDirector` (`brain/mission/demo_gz_scene.py`)
if **all** of the following hold: `BYTEWOLF_DEMO_GZ_CAMERA_MODE` is explicitly
`follow` or `overview` (unset or `off` leaves the default unchanged);
`BYTEWOLF_DEMO_GZ_VISUAL` is not `0` (same kill switch as acceptance capture);
the `gz` CLI is on `PATH`; `require_simulation_target` has accepted the
simulation endpoint and MAVSDK `connect` has succeeded (runtime target identity
is checked separately in the adapter). Typical values are
`BYTEWOLF_DEMO_GZ_CAMERA_MODE=follow` and
`BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET='-3 0 1.2'`. If the spawned model is not
`x500_0`, set `BYTEWOLF_DEMO_GZ_FOLLOW_MODEL` to the Gazebo model instance
name. The gz GUI must be running on the SITL display, and gz transport env (for
example `GZ_PARTITION`, when used) must match between the API/console process
and the simulator; otherwise setup records `gz_visual_setup` (mode, ok, error,
marker counts) and `gz_visual_setup_error` on the mission artifact when
services are missing, and the flight continues without camera or post-arm
reaffirm hooks. Applying these variables in the console systemd drop-in is an
operator decision after merge; this ticket does not change deploy units.
