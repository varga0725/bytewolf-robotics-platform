# Longer-term option: pymavlink SITL mission adapter (BYT-73)

## Problem

Mission CLIs today reach PX4 through `MavsdkMissionAdapter`, which spawns
`mavsdk_server` and exposes an **insecure gRPC API on all interfaces** with no
supported bind-host override ([mavsdk-server-grpc-listen-address.md](mavsdk-server-grpc-listen-address.md)).
Host firewall rules (`deploy/systemd/apply-mavsdk-grpc-containment.sh`) limit
exposure on the Ubuntu stack, but the process still listens broadly and adds an
extra trusted-binary dependency.

## What already exists

- **Telemetry:** `brain.cli.dashboard_telemetry --backend pymavlink` (default) is
  a passive, loopback-only MAVLink ingest (`brain.telemetry.pymavlink_relay`).
- **Preflight / params:** `brain.preflight_params.mavlink` resolves loopback UDP
  connections for read-only checks.
- **Safety:** `brain.safety.gate.py` validates missions before any adapter runs;
  that boundary must stay unchanged.

## Proposed adapter (not implemented here)

Introduce a **`PymavlinkMissionAdapter`** parallel to `MavsdkMissionAdapter` that:

1. Connects only to `udpin://127.0.0.1:<port>` (same simulation target gate as today).
2. Implements the existing mission interface (arm, takeoff, goto, land, RTL) using
   pymavlink command helpers, with **no gRPC or TCP listener**.
3. Keeps **one land fallback** and the same non-retry actuation policy as the MAVSDK path.
4. Remains behind the SafetyGate and mission CLI structure; swapping adapters would
   be a configuration or feature flag choice after contract tests prove parity on SITL.

## Why defer implementation

Parity work spans every mission type, adapter method, scenario matrix entry, and
headless evidence run. BYT-73 delivers immediate containment and verification on
the server without redesigning the mission execution surface. The pymavlink adapter
belongs in a dedicated follow-up once MAVSDK gRPC bind support or native MAVSDK
Python (v4) migration is evaluated against regression cost.

## Success criteria for a future ticket

- Headless P0.v2 matrix green with pymavlink adapter selected for nominal scenarios.
- No `mavsdk_server` process during standard stack verify (`verify-stack.sh` passes
  without relying on firewall alone).
- Unit/contract tests for mavlink command encoding and fail-closed telemetry handling.