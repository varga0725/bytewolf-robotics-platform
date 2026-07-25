# ByteWolf Command Gateway API

The local FastAPI service is the shared boundary for the web dashboard and the
future mobile client. Its conversational layer is a local Pi SDK runner backed
by NVIDIA NIM; it is the only user-interface route that may ask the Mission
Agent to create a plan. It never exposes PX4, MAVSDK, motors, or raw actuator
commands to a browser.

## Run the Control Room

```zsh
cd apps/pi_agent && npm ci --ignore-scripts && cd ../..
set -a; source .env; set +a
.venv/bin/python -m apps.api.server
```

Open `http://127.0.0.1:8080`. The page shows telemetry and camera evidence,
then provides a conversational mission interface. A flight request creates a
session-bound pending plan. The browser must explicitly approve that exact plan
before the existing safety-gated executor can connect to SITL.

## API boundary

- `GET /api/v1/telemetry` — read-only flight state.
- `GET /api/v1/camera`, `GET /api/v1/detections` — read-only vision evidence.
- `POST /api/v1/chat` — conversational request; may create a pending plan.
- `POST /api/v1/plans/approve`, `/cancel` — operate only on that browser
  session's pending plan.

Pi persists the conversation and explicitly admitted, non-sensitive user facts
under the Git-ignored `var/pi-agent/` directory, keyed by the browser-generated
local session UUID. Network authentication, mobile credentials, and remote
deployment are deliberate next steps; this server is local-only on `127.0.0.1`.
See [`docs/pi-agent-v0_1.md`](../../docs/pi-agent-v0_1.md) for the tool and
safety boundary.
