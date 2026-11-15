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
| `BAT_CRIT_THR` | `0.08` | 8 % critical → RTL path with `COM_LOW_BAT_ACT=3` (wider gap before emergency). |
| `BAT_EMERGEN_THR` | `0.05` | 5 % emergency → Land. |
| `SIM_BAT_DRAIN` | `240` | Seconds for a full 100→0 % sim discharge while armed; slower drain keeps critical→RTL observable before emergency land. |
| `SIM_BAT_MIN_PCT` | `0` | Allow the sim battery to reach emergency threshold during tests. |

## How it is applied

1. **Launchers** — after the daemon starts:
   `./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base` (or `run_px4_gazebo.zsh` standalone paths)
   retry `python -m simulation.px4.apply_sitl_failsafe_profile` until the px4-param client answers.
2. **Headless scenario runner** — `simulation/scenarios/scenarios.py` applies the same profile once SITL is ready, before per-scenario fault injection.

Skip with `BYTEWOLF_SKIP_SITL_FAILSAFE_PROFILE=1` (debug only).

## Status file and verify

Each disposable PX4 run directory (`PX4_RUN_DIR`, or the scenario runner’s `px4-sitl-run/`)
receives **`sitl-failsafe-profile.status`**:

- First line: `applied`, `failed`, or `skipped`
- Further lines: human-readable reason (gate refusal, px4-param error, etc.)

**Consumers must check this before flight or parameter review** — for example the BYT-66
in-flight harness and the preflight checker on a live SITL URL should confirm `applied` (or run
verify below) before treating geofence/failsafe rows as meaningful.

Verify read-back without writing:

```bash
.venv/bin/python -m simulation.px4.apply_sitl_failsafe_profile \
  --px4-build-directory PX4-Autopilot/build/px4_sitl_default \
  --px4-run-directory "$PX4_RUN_DIR" \
  --verify
```

Exit codes: `0` verified, `1` client/transport, `2` gate or parameter mismatch, `3` status file
missing or not `applied`.

Launcher failures print `BYTEWOLF_SITL_FAILSAFE_PROFILE_FAILED` on stderr and leave the status
file under `PX4_RUN_DIR`.

## Guard

[`simulation/px4/sitl_failsafe_profile.py`](../simulation/px4/sitl_failsafe_profile.py) reads `SYS_AUTOSTART`, `SIM_GZ_EN`, and/or `SIH_VEHICLE_TYPE` through PX4's local client, reuses `collect_param_based_sitl_signals`, and raises `SitlFailsafeProfileError` when fewer than two indicators match. No MAVLink parameter write happens without that check.

Manual apply (with SITL already running):

```bash
.venv/bin/python -m simulation.px4.apply_sitl_failsafe_profile \
  --px4-build-directory PX4-Autopilot/build/px4_sitl_default
```

## In-flight acceptance harness (BYT-66)

After headless or GUI SITL is running with the profile **applied**, run the
selectable scenario harness:

```bash
export BYTEWOLF_MISSION_EXECUTION_ENABLED=true
export PX4_RUN_DIR=/path/to/px4-sitl-run   # must contain sitl-failsafe-profile.status

.venv/bin/python -m brain.cli.acceptance_inflight_sitl --scenario all
```

Scenarios (`--scenario` is repeatable; default is `all`):

| Scenario | What it checks |
|----------|----------------|
| `datalink-loss` | ~20 m takeoff, GCS heartbeat stop, RTL after `COM_DL_LOSS_T`, land + disarm |
| `geofence` | Goto beyond `GF_MAX_HOR_DIST`, RTL within fence + documented SITL tolerance |
| `kill-in-air` | Normal disarm rejected in air; QGC-style emergency kill disarms within 1 s (**SITL only**) |
| `low-battery` | Sim drain from profile: warning, RTL at critical, land at emergency (bounded timeout) |
| `relay-inflight` | pymavlink relay on **14550** (MAVSDK stays on **14540**): `captured_at` age &lt; **3 s** default (`--relay-max-capture-age-s`), `in_air` false→true→false, loopback UDP only |

Evidence JSON per scenario is written under `simulation/artifacts/acceptance-inflight-sitl/`
(`<scenario>.json` plus `summary.json`). Each file records `outcome` (`PASS`/`FAIL`),
`profile_check`, `timings`, `transitions` (flight mode / armed / in_air samples), and
`failure_reason` when present. Exit code is non-zero if any selected scenario fails.

**Precondition (mandatory):** the harness reads `PX4_RUN_DIR/sitl-failsafe-profile.status`
(first line `applied`) and read-backs every profile parameter (same checks as
`apply_sitl_failsafe_profile --verify`). If either step fails, it writes `FAIL` evidence and
never arms.

Unit tests (no network):

```bash
.venv/bin/python -m unittest tests.test_acceptance_inflight_sitl -v
```

Opt-in live relay scenario (requires SITL URL and applied profile):

```bash
export BYTEWOLF_SITL_CONNECTION=udpin://127.0.0.1:14540
export BYTEWOLF_MISSION_EXECUTION_ENABLED=true
.venv/bin/python -m unittest tests.test_acceptance_inflight_sitl.AcceptanceInflightSitlLiveTests -v
```