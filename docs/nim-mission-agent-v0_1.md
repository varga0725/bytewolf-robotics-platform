# NVIDIA NIM Mission Agent v0.1

The Mission Agent turns a typed natural-language request into a high-level
MissionSpec proposal through NVIDIA's hosted NIM API. It is an AI planning
boundary, not a flight-control boundary: the agent has no MAVSDK, ROS-control,
MAVLink, actuator, or motor access.

## Configuration

Create a local `.env` file (ignored by Git) with these values. Never commit an
API key.

```sh
NVIDIA_API_KEY=replace-with-your-key
NIM_MISSION_MODEL=nvidia/nemotron-3-nano-30b-a3b
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
```

The hosted API is OpenAI-compatible. The model is forced to call the single
`propose_mission_spec` tool, and may propose only an `intent` and `steps`.
The gateway itself creates the mission ID and supplies the active vehicle ID,
hard limits, link-loss action, and abort policy from the versioned twin profile.

## Safety boundary

Before a PX4 connection can be opened, every proposal passes all of these gates:

1. strict tool-argument JSON parsing;
2. MissionSpec JSON Schema validation;
3. deterministic platform-limit and SafetyGate validation;
4. executable-shape validation.

Today only these lossless execution shapes are accepted:

- `TAKEOFF → HOLD → LAND`
- `TAKEOFF → GOTO_LOCAL → HOLD → LAND`
- `TAKEOFF → HOLD → RTL`

Any NIM failure, malformed response, unsupported shape, ambiguous waypoint, or
safety violation is a refusal with **no PX4 connection and no actuation**.

## Run it

First start PX4 SITL/Gazebo in one terminal. In another, source local secrets
without printing them and make a dry run:

```zsh
set -a; source .env; set +a
.venv/bin/python -m brain.cli.fly_nim_mission \
  --command "Szállj fel 2 méterre, lebegj 3 másodpercig, majd szállj le."
```

The dry run writes the normalized plan plus a sibling `.approval.json` record
containing its SHA-256 hash. Execute **that exact, unchanged file** in a
separate command; this step never calls NIM again and refuses a missing or
hash-mismatched approval record:

```zsh
.venv/bin/python -m brain.cli.fly_nim_mission \
  --mission-spec-file simulation/artifacts/agent-missions/<mission-id>.mission-spec.json \
  --execute
```

`--execute` is the only branch that constructs a MAVSDK adapter. It still runs
the adapter's telemetry preflight, runtime watchdog, bounded landing fallback,
and immutable mission artifact flow.
