# DJI-inspired product direction — v0.1

## Decision

ByteWolf is moving toward the clarity and predictability associated with a
modern integrated drone product: camera, map, telemetry, vehicle state, mission
review, and evidence should feel like one coherent operator workflow. The
Notion strategy also evaluates a DJI Enterprise product track built around
Dock 3, Matrice 4TD, FlightHub/Cloud API, and Thermal SDK. This document keeps
that product track separate from the repository's existing PX4/X500 engineering
track. It is not a clone of DJI software, visual trade dress, terminology,
proprietary protocols, or autonomous behaviour.

The current product is still a safety-first PX4 digital-twin platform. The
physical X500 V2 and Hawkeye camera exist, but there is no flight battery or
onboard companion computer/Jetson, and physical actuation is disabled in the
versioned twin. Nothing in this direction document changes that status.

## Two tracks, one safety model

- **X500/PX4 engineering track:** the code and evidence in this repository.
  It is the digital-twin, safety-contract, simulation, telemetry, and server-
  control laboratory. The physical X500 is an unfinished development platform,
  not the approved commercial remote-operations vehicle.
- **DJI Enterprise product track:** an early validation path for industrial RGB
  and thermal inspection and later human-supervised site operations. DJI
  hardware, FlightHub/Cloud API, Thermal SDK licensing, customer workflow, and
  regulatory assumptions are still separate decisions. There is no DJI vehicle
  integration or physical-flight evidence in this repository today.

Safety concepts may be shared, but proof does not transfer automatically. An
X500 SITL test does not validate a DJI Cloud API operation, and a DJI product
capability does not authorize the X500.

## Experience principles

1. **State before controls.** Connection, deployment mode, vehicle identity,
   freshness, armed/in-air state, GNSS, battery, and active safety limits are
   visible before an operator can review an action.
2. **Camera, map, and telemetry agree.** Each view carries capture time,
   freshness, source, and proof level. Missing or stale evidence stays visibly
   missing; it is never inferred into a healthy state.
3. **Progressive disclosure.** The default surface explains readiness and the
   next safe step. Detailed contracts, raw evidence, and diagnostics remain
   available without turning the main view into a developer console.
4. **Proposal is not execution.** Natural language, map clicks, perception, and
   learned models may propose a bounded MissionSpec. A deterministic safety
   decision and a separate human approval are required before execution.
5. **No raw actuator surface.** The product does not expose motors, actuator
   channels, unconstrained joystick commands, or direct MAVLink endpoints to a
   browser or model.
6. **Proof-labelled outcomes.** Unit/fake, contract, SITL, hardware bench, and
   physical-flight evidence are distinct labels and never silently promoted.

## Current deliverable boundary

- The local Control Room can show validated telemetry, camera and world
  evidence, mission proposals, approvals, and audit replay.
- The command gateway is local-only on `127.0.0.1`; network authentication and
  RBAC are not implemented, so remote ingress is disabled.
- Mission execution is pinned to `simulation` by the deployed gateway and
  systemd configuration.
- Flight CLIs accept a local UDP SITL endpoint in simulation mode. Read-only
  bench and physical telemetry use explicit modes that cannot actuate.
- The shipped X500 twin sets `safety.physical_actuation.enabled: false`.

## Staged product path

| Stage | Operator experience | Required proof |
| --- | --- | --- |
| 0. Offline | Replay, schemas, UI states, and deterministic safety decisions | Unit/contract tests only |
| 1. Observe simulation | Live SITL telemetry, cameras, map, and clear freshness | Repeatable, isolated SITL evidence |
| 2. Propose and review | Chat/map request becomes an inspectable, bounded MissionSpec | Hash-bound review and rejection tests |
| 3. Execute simulation | Explicit approval starts only the reviewed SITL plan | Simulator identity/attestation, audit, recovery evidence |
| 4. Observe hardware | Server reads a registered vehicle over a read-only path | UID allowlist, firmware decision, bench evidence, authenticated access |
| 5. Physical actuation | A later, platform-specific release may offer bounded flight controls | Named target platform, measured/official safety data, independent failsafes, site checklist, two-person authorization, regulatory approval, full physical test campaign |

Stage 5 is not approved or implemented by this document. In particular, there
is no one-tap physical takeoff, inferred obstacle-avoidance guarantee, or remote
emergency-control claim in the current release.

## Superseded UX claims

`docs/bytewolf_ux_ui_architecture.md` is an aspirational historical design
source. Any delivered-control claim in that document is superseded by this
product direction, `docs/frontend-control-room-plan.md`, and
`docs/server-control-safety-v0_1.md` until it is backed by implementation and
the stated proof level.
