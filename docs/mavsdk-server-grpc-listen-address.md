# MAVSDK `mavsdk_server` gRPC listen address (BYT-46)

## Question

Can the pinned MAVSDK stack bind the telemetry relay's `mavsdk_server` gRPC
listener to loopback (`127.0.0.1`) instead of all interfaces?

## Facts (mavsdk Python wheel **3.17.4**, constraint `mavsdk>=2.6,<4`)

### Bundled binary CLI

The wheel ships
`.venv/lib/python3.12/site-packages/mavsdk/bin/mavsdk_server`. Its help
documents gRPC options as port only:

```
Options:
  -h | --help : show this help
  -p          : set the port on which to run the gRPC server,
                set to 0 to choose a free port automatically
                (default is 50051)
  --sysid     : ...
  --compid    : ...
```

There is **no** flag for a gRPC bind host (no `--grpc-host`, `--listen`, or
similar). MAVLink URL schemes (`udpin://[bind_host][:bind_port]`, etc.) control
where MAVLink is received, not where gRPC listens.

### Upstream source (MAVSDK **v3.17.4**)

`src/mavsdk_server/src/grpc_server.cpp` hard-codes the gRPC listen address:

```cpp
void GrpcServer::setup_port(grpc::ServerBuilder& builder)
{
    const std::string server_address("0.0.0.0:" + std::to_string(_port));
    builder.AddListeningPort(server_address, grpc::InsecureServerCredentials(), &_bound_port);
}
```

On success the server logs `Server set to listen on 0.0.0.0:<port>`.

### MAVSDK-Python `System()` behaviour

When `mavsdk_server_address` is left unset, `System.connect()` spawns the
bundled binary with `-p <port>` only (`mavsdk/system.py`). The docstring says
"listening on localhost" but that describes the **client** default
(`localhost`), not the server bind — the C++ server still listens on `0.0.0.0`.

Starting an external `mavsdk_server` subprocess from the relay and connecting
with `System(mavsdk_server_address="127.0.0.1", port=…)` would use loopback on
the **client** side only; the gRPC server would remain on `0.0.0.0:<port>`.

## Conclusion for BYT-46

Loopback-only gRPC for `mavsdk_server` is **not supported** in the pinned MAVSDK
release without forking MAVSDK or switching away from the gRPC stack (out of
scope for this ticket). Remediation options belong in a follow-up (upstream
feature, MAVSDK pin with a bind-host flag, or an architecture change explicitly
approved outside BYT-46).

## BYT-73 containment (host firewall)

Evidence from this repo (wheel help and upstream `grpc_server.cpp`, sections
above) shows no supported gRPC bind-host flag. **Option (a) is unavailable** on
the pinned stack.

The Ubuntu stack installer therefore applies a **narrow, idempotent iptables/ip6tables
INPUT rule** (`deploy/systemd/apply-mavsdk-grpc-containment.sh`) that drops only
non-loopback sources targeting MAVSDK gRPC ports (`50051` and `51000–60999`, the
same range the headless scenario runner allocates). The rule carries comment
`bytewolf-mavsdk-grpc-loopback`, inserts with `-I` only when `-C` reports it is
missing, and does not flush or rewrite unrelated policy.

`deploy/systemd/verify-stack.sh` still fails closed when `ss -ltnp` shows
`mavsdk_server` bound to a non-loopback address (for example `0.0.0.0:50051`), so
an operator cannot mistake an uncontained listener for an acceptable posture.

The always-on dashboard relay defaults to **pymavlink** and does not spawn
`mavsdk_server`. Missions and `--backend mavsdk` still do; containment limits who
can reach those listeners while upstream lacks a true loopback bind.

## Longer-term direction

See [pymavlink-sitl-mission-adapter.md](pymavlink-sitl-mission-adapter.md) for a
pymavlink-based mission path that would remove the gRPC listener from routine
SITL flights without relaxing the safety gate.

## BYT-49 default (pymavlink backend)

The dashboard telemetry relay (`brain.cli.dashboard_telemetry`) now defaults to
`--backend pymavlink`: passive MAVLink ingest on `udpin://127.0.0.1:14540` with
**no** TCP or gRPC listener. The previous MAVSDK path remains available:

```bash
.venv/bin/python -m brain.cli.dashboard_telemetry
.venv/bin/python -m brain.cli.dashboard_telemetry --backend mavsdk
```

`--backend mavsdk` still spawns `mavsdk_server` with gRPC on `0.0.0.0` as
documented above. The pymavlink backend sends no GCS heartbeat; PX4 may warn
about a missing GCS link in SITL while the dashboard relay is passive-only.
