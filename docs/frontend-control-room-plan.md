# Frontend Control Room — implementation plan

## Goal

Replace the monolithic browser script with a typed React operator application
while preserving the existing FastAPI API and the safety boundary: the browser
may read state, inspect evidence, propose a mission, and approve its own
reviewed plan. It must not gain a direct PX4, MAVSDK, actuator, RTL, LAND,
HOLD, or kill-switch endpoint.

The product is an observable operations interface, not a decorative cockpit:
clarity over spectacle, evidence over hype, and a consistent view of freshness,
safety, confidence, and uncertainty. The visual direction is deliberately
restrained, engineering-led, and accessible rather than gamer, cyberpunk, or
military themed.

## Architecture decision

- **Frontend:** React 18, TypeScript, Vite and Vitest.
- **Serving:** Vite development server proxies `/api` to FastAPI; production
  builds are served by `apps.api.server`.
- **Styling:** semantic CSS custom-property tokens with responsive,
  component-scoped layouts. Tailwind, Mapbox and a native mobile client remain
  explicit future decisions, not implied dependencies.
- **Source of truth:** API payloads are rendered only after validation. Safety
  limits, geofence geometry and map routes are never reimplemented in the
  browser.
- **Evidence model:** stale, missing, disputed, malformed, or insufficiently
  evidenced data is visibly withheld or marked; it is never promoted to an
  operational fact.
- **Cognitive context:** the UI may show auditable inputs, outcomes, and
  bounded summaries, but never a hidden chain of thought or an unreviewed
  execution path.

## Delivery status

| Slice | Status | Delivered scope |
| --- | --- | --- |
| 1. Foundation | Complete | Vite/React/TypeScript, typed telemetry client, deployable FastAPI-served build, accessible Control Room shell, stale/unavailable telemetry states and integrity summary. |
| 2. Read-only evidence | Complete | Camera detections with evidence state, measured occupancy map, personal memory, world claims, separate personal/world knowledge graphs, and immutable audit-artifact mission replay. |
| 3. Reviewed missions | Complete | Conversational mission proposal, point and survey review, safety-envelope display, session-bound approval/cancellation, execution-status monitoring, and explicit failure state when execution can no longer be verified. |
| 4. Operator-grade UX | Substantially complete | Semantic design tokens, responsive layouts, keyboard-operable tabs, Ctrl/Cmd+K quick navigation, focus/error handling, consistent visual state system, and Chromium E2E smoke coverage for stale telemetry, read-only knowledge navigation, the explicit mission-approval boundary, and execution-status loss. |

### Completed product constraints

- Replay is read-only and is available only from a validated audit artifact
  joined to run-bound telemetry history; the UI never exposes replay as a
  control action.
- Maps remain measured-occupancy only. The mission map withholds cells unless
  the payload explicitly declares `occupancy_only` and each rendered cell is
  valid.
- Personal memory and world evidence are separate domains in both the data
  model presentation and the Knowledge view. Disputed evidence is not rendered
  as confirmed knowledge.
- AI suggestion/review and operator approval remain distinct states. No
  emergency-control interface is present.
- Keyboard focus, non-colour status cues, and failure messages are product
  behavior, not optional decorative affordances.

## Remaining delivery goals

1. **Expand browser E2E coverage** — the Playwright smoke suite now verifies
   stale telemetry, read-only Knowledge navigation, the explicit
   mission-approval boundary, and execution-status loss with all routes
   mocked. Add coverage for evidence withholding, cancellation, replay
   inspection, and keyboard focus behavior against an isolated simulation
   fixture.
2. **Explicit mobile product decision** — decide and document whether mobile
   is a read-only/on-call companion, a restricted approval surface, or not a
   supported operator platform. Then validate the selected scope on actual
   handset viewports and assistive technology. Responsive CSS alone must not
   imply safe in-flight mobile operation.
3. **Operational hardening** — define the authentication, authorization,
   audit-retention, and multi-robot/fleet boundaries before exposing the
   Control Room beyond the local simulation environment. These are a separate
   product/security slice, not frontend-only additions.
4. **Notification strategy** — decide whether server-mediated notifications
   are needed after the authorization and fleet model exists. Push
   notifications are intentionally not implied by the current polling UI.

## Acceptance criteria

### Delivered baseline

- `npm run test` covers telemetry formatting, evidence validation, visual
  states, navigation, mission/replay behavior, and the knowledge boundary.
- `npm run build` produces a deployable static app.
- FastAPI serves the built application without shadowing `/api/v1/*` routes.
- Existing Python API tests remain green.
- Telemetry, evidence, and execution state fail visibly rather than appearing
  current or successful when their status cannot be established.

### Before the operator UX is declared complete

- Playwright E2E tests cover the critical browser flows against the served
  application, including an isolated reviewed-mission lifecycle without a live
  vehicle.
- The mobile decision has an explicit documented capability boundary and
  corresponding viewport/accessibility validation.
- New API-backed views keep the same validation and evidence-withholding
  conventions as the existing Control Room.

## Deferred scope / non-goals

- Direct in-flight commands, fleet management, authentication/authorization,
  server push notifications, and a native mobile app are not part of the
  current simulation Control Room release.
- A map provider or a free-space representation is not implied; the current
  world map remains measured-occupancy only.
- Replay UI is no longer a non-goal: the read-only, audit-backed replay view is
  delivered. It must remain non-controlling.
