# SITL identity gate before arming (BYT-76)

[`require_px4_sitl_indication`](../brain/mission/sitl_target.py) runs after MAVSDK
connect and immediately before any `action.arm()` on paths that can start flight.
Insufficient proof from PX4 **simulator-specific** parameter and identity signals
(at least two independent SITL indicators per `sitl_target.py`, not name/substring
heuristics alone), timeouts, or read errors raise `DemoPointRtlError` and **no
actuation** (`arm`, altitude parameter writes, offboard fallbacks) runs
(fail-closed).

Loopback / deployment checks and physical-actuation locks are separate; this
document lists only the SITL multi-signal gate.

## `MavsdkMissionAdapter` (`brain/adapters/mavsdk_adapter.py`)

All `execute_*` missions that arm or take off call `_require_px4_sitl_before_arm`
(after preflight, before any `set_takeoff_altitude` / `set_return_to_launch_altitude`
and before `arm`):

| Method | Approx. gate line |
|--------|-------------------|
| `execute` | before first `arm` |
| `execute_controlled_interruption_mission` | before first `arm` |
| `execute_waypoint_mission` | before first `arm` |
| `execute_target_approach_mission` | before first `arm` |
| `execute_waypoints_mission` | before first `arm` |
| `execute_waypoint_square_mission` | before first `arm` |
| `execute_return_to_home_mission` | before first `arm` |
| `execute_waypoints_return_to_home_mission` | before first `arm` |
| `execute_demo_point_rtl_mission` | before first `arm` (same helper) |

`verify_preflight` and offboard/goto helpers do not arm.

## Demo Mission-plugin adapter

[`execute_demo_waypoint_mission`](../brain/adapters/demo_waypoint_mission_adapter.py)
already gated pre-arm (unchanged in BYT-76).

## Headless / acceptance sim runners (direct `action.arm`)

| Module | Function | Gate before `arm` |
|--------|----------|-------------------|
| `simulation/control/avoidance_route_run.py` | `_fly_avoidance_route` | yes |
| `simulation/control/mission_route_run.py` | `_fly_route` | yes |
| `simulation/control/shield_run.py` | shield fly path | yes |
| `simulation/control/offboard_watchdog_run.py` | `_fly` | yes |

## Covered via adapter (no extra direct gate)

| Entry | Reason |
|-------|--------|
| `simulation/perception/autonomous_approach.py` | `MavsdkMissionAdapter.execute_target_approach_mission` |
| `brain/cli/fly_*` mission CLIs | `MavsdkMissionAdapter` (out of scope for BYT-76 edits) |

## Other `action.arm()` call sites (non-test)

| Location | Status |
|----------|--------|
| `brain/adapters/demo_waypoint_mission_adapter.py` | gated (pre-existing) |
| `brain/adapters/mavsdk_adapter.py` | gated via `_require_px4_sitl_before_arm` |

No additional production `action.arm()` sites were found outside tests and
artifact JSON traces.