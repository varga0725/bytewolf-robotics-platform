# Read-only PX4 preflight parameter checker

Prototype tooling for Holybro X500 V2 / PX4 v1.17 setups flown **without an RC transmitter**, using QGroundControl, a PS5 joystick over `MANUAL_CONTROL`, and a SiK telemetry radio. The checker downloads or loads parameters and prints a **PASS / WARN / FAIL** report. It never writes parameters, never arms, and never uploads missions.

Real flight-controller use is **read-only over the telemetry or USB MAVLink link**. The platform `physical_actuation` lock and mission execution gate are unchanged.

## Usage

```bash
# Over MAVLink (simulation or bench; default deployment mode is simulation)
.venv/bin/python -m brain.preflight_params \
  --connection 'udpin://0.0.0.0:14540' \
  --json simulation/artifacts/preflight-report.json

# Offline from a QGC parameter export
.venv/bin/python -m brain.preflight_params \
  --params-file /path/to/vehicle.params

# Accept Land (3) instead of Return (2) for datalink loss
.venv/bin/python -m brain.preflight_params \
  --connection 'udpin://0.0.0.0:14540' \
  --accept-nav-dll-land
```

**Exit codes:** `0` — no FAIL rows; `1` — at least one FAIL; `2` — connection or file/read error.

For a physical FC on the telemetry radio, set `--deployment-mode physical_telemetry` and the serial/USB bench path documented in `docs/hardware-bringup-x500v2.md`.

### Optional SITL integration test

```bash
export BYTEWOLF_SITL_CONNECTION='udpin://0.0.0.0:14540'
export BYTEWOLF_SITL_CONNECTION_TIMEOUT=60   # optional
.venv/bin/python -m unittest tests.test_preflight_params_sitl -v
```

Stock PX4 SITL defaults often **FAIL** several rules (for example `NAV_DLL_ACT=0`). The integration test asserts structure and detection, not an all-PASS report.

## Rule table

| Parameter | Expected | Severity | Rationale |
| --- | --- | --- | --- |
| `NAV_DLL_ACT` | `2` (Return), or `3` (Land) with `--accept-nav-dll-land` | core / FAIL | SiK datalink loss must not stay Disabled (`0`). |
| `COM_DL_LOSS_T` | `> 0` and `≤ 10` s | core / FAIL | Datalink-loss timeout before failsafe. |
| `COM_RC_IN_MODE` | `1` (MAVLink only) | core / FAIL | Joystick-only operation without RC checks. |
| `COM_LOW_BAT_ACT` | `3` | core / FAIL | Critical low battery must RTL/Land, not warning-only. |
| `GF_ACTION` | `≠ 0` | core / FAIL | Geofence breach must trigger action. |
| `GF_MAX_HOR_DIST` | `> 0` m | core / FAIL | Horizontal geofence enabled (reports actual value). |
| `GF_MAX_VER_DIST` | `> 0` m | core / FAIL | Vertical geofence enabled (reports actual value). |
| `COM_ARM_WO_GPS` | `0` (Deny) | extra / WARN | Informational beyond core list. |
| `NAV_RCL_ACT` | (reported) | extra / WARN | Manual-control-loss action visibility. |
| `COM_RC_LOSS_T` | (reported) | extra / WARN | Manual-control-loss timeout visibility. |

Missing parameters are **FAIL** with reason `not readable`.

## Safety

- Only `PARAM_REQUEST_LIST` / `PARAM_VALUE` MAVLink traffic is used on the wire.
- No `PARAM_SET`, arming, mode changes, mission upload, or actuator commands.
- Unit tests statically scan `brain/preflight_params/` for forbidden write/command APIs.

This prototype has **not** been verified on the real Holybro flight controller yet.