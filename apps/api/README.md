# ByteWolf Command Gateway API

The local FastAPI service is the shared boundary for the authenticated Control
Room and the later mobile client. It also serves the separate public marketing
site at `/`. Its conversational layer is a local Pi SDK runner backed
by NVIDIA NIM; it is the only user-interface route that may ask the Mission
Agent to create a plan. It never exposes PX4, MAVSDK, motors, or raw actuator
commands to a browser.

## Run the public site and Control Room

```zsh
cd apps/marketing/frontend && npm ci && npm run build && cd ../../..
cd apps/dashboard/frontend && npm ci && npm run build && cd ../../..
.venv/bin/python -m apps.api.server
```

Open `http://127.0.0.1:8080/` for the public marketing site and
`http://127.0.0.1:8080/control-room/` for the React Control Room. The Control
Room provides read-only state, camera, world evidence, memory, knowledge-graph
and audit-replay views, plus reviewed mission planning. The legacy static
dashboard has been removed.

Only the Control Room uses the mission API boundary: a flight request creates
a session-bound pending plan, and the browser must explicitly approve that
exact plan before the existing safety-gated executor can connect to SITL. The
public site has no telemetry, control, approval, or authentication surface.

## API boundary

- `GET /api/v1/telemetry` — read-only flight state.
- `GET /api/v1/camera`, `GET /api/v1/detections` — read-only vision evidence.
- `GET /api/v1/world-map`, `/world-memory`, `/knowledge` — read-only evidence
  and separate personal/world knowledge context. `/knowledge` is session-bound.
- `GET /api/v1/missions/replays`, `/missions/replays/{run_id}` — validated,
  read-only mission audit replay; these endpoints never contact a vehicle.
- `POST /api/v1/chat` — conversational request; may create a pending plan.
- `POST /api/v1/plans/approve`, `/cancel` — operate only on that browser
  session's pending plan.

Validate the Control Room with `npm run test`, `npm run test:e2e`, and
`npm run build` from `apps/dashboard/frontend`; validate the public site with
its `npm run test` and `npm run build` commands. The Control Room browser E2E
suite mocks only read APIs and does not approve or execute a mission.

Pi persists the conversation and explicitly admitted, non-sensitive user facts
under the Git-ignored `var/pi-agent/` directory, keyed by the browser-generated
local session UUID. Network authentication, mobile credentials, and remote
deployment are deliberate next steps; this server is local-only on `127.0.0.1`.
See [`docs/pi-agent-v0_1.md`](../../docs/pi-agent-v0_1.md) for the tool and
safety boundary.
