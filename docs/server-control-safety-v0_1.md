# Safe server control boundary — v0.1

## Current decision

The server may progressively observe and control **simulation**, while physical
flight remains disabled. No code path, document, or operator permit in this
release authorizes arming, takeoff, motor output, Offboard delivery, or another
actuation command on the physical X500 V2.

This separation matters because the airframe exists but the flight battery and
onboard companion computer/Jetson do not. The observed physical controller is
running PX4 v1.16.0 while the SITL baseline is v1.17.0, and that mismatch has no
compatibility decision yet.

## Enforcement shipped in v0.1

All MAVSDK CLI entry points declare an explicit deployment mode and validate it
before importing MAVSDK, acquiring the shared link lease, constructing a
vehicle client, or opening an endpoint.

| Mode | Connection rule | Actuation rule |
| --- | --- | --- |
| `simulation` | Loopback `udpin`/`udpout` only; wildcard, remote, serial, and TCP endpoints are rejected | Bounded mission CLIs may execute after their existing SafetyGate and review checks |
| `bench_readonly` | Direct serial device only | Always rejected |
| `physical_telemetry` | Explicit supported physical endpoint | Always rejected |
| `physical_actuation` | Explicit supported endpoint | Rejected by the shipped twin before a permit can matter |

The six connected mission CLIs use this boundary: takeoff/hover/land, waypoint,
RTL, waypoint square, controlled interruption, and reviewed NIM MissionSpec.
USB bench diagnostics, pre-arm diagnostics, dashboard telemetry, and the ROS 2
telemetry bridge use the read-only boundary.

The Telegram/Control Room gateway always invokes the mission CLI with
`--deployment-mode simulation`; an HTTP request cannot select a mode or
endpoint. The systemd deployment pins the same mode. The installer keeps the
API on `127.0.0.1` and does not publish it through Tailscale or another proxy.

## Dormant physical permit contract

The repository defines a fail-closed permit format for a future review. It is
single-use, valid for at most 15 minutes, requires distinct operator and safety
observer identities, and binds the exact vehicle ID, endpoint, operation,
normalized request hash, and safety-profile hash. The permit is atomically
consumed before a connection could open.

A permit is deliberately **necessary but not sufficient**. With
`safety.physical_actuation.enabled: false`, every physical actuation request is
rejected even if a structurally valid permit is supplied. An independent
software-release lock is also hard-coded closed, so pointing a CLI at an
alternate YAML profile cannot unlock the dormant path. No enablement recipe or
ready-to-use permit is shipped.

## Known gaps before any broader control release

1. Loopback proves locality, not simulator identity. Simulation actuation still
   needs a short-lived launcher attestation bound to the PX4 process, binary
   hash, port, and run ID.
2. Physical read-only mode still needs to bind the observed MAVLink hardware
   UID to the local fleet registry and an explicit vehicle allowlist.
3. The lower-level mission and Offboard adapters need a non-forgeable,
   deployment-authorized session object so a new caller cannot bypass CLI
   wiring. Active Offboard remains disabled meanwhile.
4. Mission artifacts need a contract revision that records deployment mode,
   simulator run ID or physical UID, policy hash, and approval/permit ID for
   both accepted and rejected attempts.
5. Remote access needs application authentication, RBAC, replay/rate
   protection, audit attribution, and a separately reviewed ingress design.
6. Physical readiness needs the battery and companion-computer decisions,
   measured dynamics/endurance, firmware compatibility, independent failsafes,
   a site procedure, and a staged bench/tethered/flight validation campaign.

Until these gates close, physical actuation must remain disabled.

The DJI Enterprise strategy is a separate integration boundary. A future
FlightHub or Cloud API adapter must receive its own target identity, credential,
approval, replay protection, audit, and sandbox contract; the MAVSDK/PX4 mode or
permit defined here must never be treated as authorization for a DJI operation.

## Hardware-independent test order

1. Run deployment-policy and CLI-wiring tests. They use no MAVSDK process,
   simulator, or vehicle.
2. Run the complete Python unit suite. Its MAVSDK/PX4 collaborators are fakes.
3. Compile all Python modules and run `git diff --check` plus shell syntax
   checks for deployment scripts.
4. Run frontend unit/build checks with mocked read APIs.
5. Only in a separately scheduled integration window, start an isolated PX4
   SITL/Gazebo instance and verify simulator attestation, review binding,
   command sequencing, audit evidence, and bounded recovery.

The current safe commands for steps 1–3 are:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_deployment*.py' -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q brain apps simulation tests
bash -n deploy/systemd/install-stack.sh deploy/systemd/verify-stack.sh
git diff --check
```

None of these commands starts PX4/Gazebo or opens a vehicle connection.
