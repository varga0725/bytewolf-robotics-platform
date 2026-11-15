# SITL-only failsafe parameter profile (PX4 v1.17.0)

Headless PX4 SITL boots with stock defaults: geofence distances are **0** (disabled),
datalink-loss action is often **Disabled**, and the preflight checker ([`docs/preflight-params-checker.md`](preflight-params-checker.md))
correctly FAILs those rows. Before in-flight SITL behavioural tests mean anything,
the platform applies `simulation/px4/params/sitl-failsafe.params` **only** when the
multi-signal SITL gate ([`brain/mission/sitl_target.py`](../brain/mission/sitl_target.py), PR #18)
sees at least two independent indicators (e.g. `SYS_AUTOSTART` in the gz/SIH POSIX range and
`SIM_GZ_EN=1`). The apply path refuses a physical FC that does not match.

## Values (pinned to PX4 v1.17.0)

| Parameter | Value | Source / rationale |
|-----------|-------|-------------------|
| `GF_MAX_HOR_DIST` | `100` | [Geofence params](https://github.com/PX4/PX4-Autopilot/blob/v1.17.0/src/modules/geofence/geofence_params.c); non-zero enables horizontal cylinder (m). |
| `GF_MAX_VER_DIST` | `50` | Same; vertical limit (m). |
| `GF_ACTION` | `3` | v1.17 enum **Return mode** on breach ([Safety — Geofence](https://docs.px4.io/v1.17/en/config/safety)). |
| `NAV_DLL_ACT` | `2` | **Return mode** for GCS datalink loss (`commander_params.c`). |
| `COM_DL_LOSS_T` | `10` | 10 s datalink-loss timeout (within preflight rule max). |
| `COM_LOW_BAT_ACT` | `3` | v1.17: Return at critical, Land at emergency (`commander_params.c`). |
| `BAT_LOW_THR` | `0.15` | 15 % warning (`battery` module). |
| `BAT_CRIT_THR` | `0.07` | 7 % critical → RTL path with `COM_LOW_BAT_ACT=3`. |
| `BAT_EMERGEN_THR` | `0.05` | 5 % emergency → Land. |
| `SIM_BAT_DRAIN` | `180` | Seconds for a full 100→0 % sim discharge while armed (~3 min to critical in flight). |
| `SIM_BAT_MIN_PCT` | `0` | Allow the sim battery to reach emergency threshold during tests. |

## How it is applied

1. **Launchers** — after the daemon starts:
   `./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base` (or `run_px4_gazebo.zsh` standalone paths)
   retry `python -m simulation.px4.apply_sitl_failsafe_profile` until the px4-param client answers.
2. **Headless scenario runner** — `simulation/scenarios/scenarios.py` applies the same profile once SITL is ready, before per-scenario fault injection.

Skip with `BYTEWOLF_SKIP_SITL_FAILSAFE_PROFILE=1` (debug only).

## Guard

[`simulation/px4/sitl_failsafe_profile.py`](../simulation/px4/sitl_failsafe_profile.py) reads `SYS_AUTOSTART`, `SIM_GZ_EN`, and/or `SIH_VEHICLE_TYPE` through PX4's local client, reuses `collect_param_based_sitl_signals`, and raises `SitlFailsafeProfileError` when fewer than two indicators match. No MAVLink parameter write happens without that check.

Manual apply (with SITL already running):

```bash
.venv/bin/python -m simulation.px4.apply_sitl_failsafe_profile \
  --px4-build-directory PX4-Autopilot/build/px4_sitl_default
```