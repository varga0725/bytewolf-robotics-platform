# World Memory v0.1

## Decision

World memory is **separate** from the personal memory the Pi post-turn hook
writes. Personal memory holds a handful of stable facts a user chose to share.
World memory holds perishable sensor and mission evidence. Mixing them would
let a detection become an identity claim, so the two stores share no file, no
category, and no code path.

`brain/memory/world_memory.py` is the store; `brain/memory/evidence.py` is the
only supported way to turn a live reading into a claim.

## Claim contract

Schema: `shared/schemas/world_memory/world_claim_v0_1.schema.json`.

```json
{
  "contract_version": "v0.1",
  "subject": "marker:red-pad",
  "category": "target_sighting",
  "statement": "A(z) 'landing-pad' célpont látható …",
  "evidence": {
    "source": "camera:down_rgb",
    "observed_at": "2026-07-20T12:00:00+00:00",
    "expires_at": "2026-07-20T12:05:00+00:00",
    "confidence": 0.95,
    "artifact": "simulation/artifacts/perception/autonomous-approach-….json"
  }
}
```

All four evidence fields are required. A claim without a source, an
observation time, a confidence, or an expiry is not admitted — an unsourced,
undated, unquantified, never-expiring belief is exactly what this store exists
to prevent. `expires_at` must be later than `observed_at`, and `confidence` is
strictly above 0: a claim nobody believes is not evidence.

Categories are `landmark`, `obstacle`, `target_sighting`, `map_region` and
`mission_outcome`. There is no person, face, or identity category, and a
statement or subject reaching for identity (`arcfelismerés`, `face recognition`,
`biometrikus`, `személyazonosság`) is rejected at admission. Face
identification stays out of scope.

## Reading is a decision

`WorldMemory.recall(now, min_confidence=…)` returns only what may be believed
at that instant:

- an **expired** claim is gone, not a slightly older fact;
- a claim below the caller's **confidence floor** never leaves the store;
- a **contradicted** subject yields no fact at all.

A later observation supersedes an earlier one about the same subject when it is
at least as confident. A later, *less* confident contradiction neither wins nor
loses: the subject becomes disputed and is reported by
`WorldMemory.disputed(now)`. Guessing between two live measurements is how a
stale detection turns into a false certainty. Agreement settles a dispute.

The log itself is append-only (`var/world-memory/claims.jsonl`, git-ignored):
a superseded observation stays recorded so the contradiction remains visible.
A malformed line is skipped on read rather than raised on — one bad record must
not make the whole world unreadable.

## Evidence sources

`brain/memory/evidence.py` converts, fail-closed:

- **vision** — a `TargetObservation` becomes a `target_sighting` only while its
  own contract says it is usable; invalid, missing, stale or confidence-free
  sightings produce nothing;
- **LiDAR** — only `measured` sectors of an obstacle observation become claims.
  A `clear` sector is a negative measurement and an `unobserved` sector is no
  measurement; remembering either as free space is how a blind spot becomes a
  flight path. The `lidar_2d_v2` rear 90° is permanently unobserved;
- **mission history** — a `MissionAuditArtifact` becomes a `mission_outcome`
  with full confidence, because a recorded run is a record, not an estimate.
  It still carries an explicit horizon: world memory holds nothing permanent.

## The map layer (`map_region`)

`brain/memory/world_map.py` anchors body-frame scans to a grid fixed at a
chosen origin, usually home. Without that anchor the memory is worthless: a
sector bearing is relative to wherever the nose pointed, so the same wall lands
somewhere new after every turn. With it, a second pass reinforces the same cell.

- The subject is self-describing — `map_region:2m:n5:e-3` — so a stored cell
  stays readable after the default cell size changes.
- Sectors that fall in one cell collapse to the most confident: two beams on
  one wall are one piece of evidence about that wall, not two.
- **Occupancy only.** A cell means "something was measured here". There is no
  free-space layer, because a `clear` sector is a negative measurement and an
  `unobserved` sector is no measurement; storing either as free space is how a
  blind spot becomes a flight path.
- A sector is a wedge, not a point: its lateral position is known to roughly
  `distance × width_deg`. Cells are therefore coarse, and each claim records
  the sector width it came from instead of implying a point measurement.
- `GET /api/v1/world-map` serves the currently believed cells with
  `occupancy_only: true`, and the dashboard draws them north-up around the
  grid origin, marking disputed cells rather than dropping them.

## Knowledge graphs — two, never one

`brain/memory/graph.py` projects the stores for the dashboard, and it builds
**two** graphs with namespaced node ids (`personal:` and `world:`). No edge may
cross the namespaces; the projection raises `GraphBoundaryError` if one tries.

This is the store contract expressed as a picture. A single edge from a person
to a detected object would turn "the camera saw a red pad" into "Ferenc's red
pad" — an identity claim the world store refuses to hold, smuggled in by the
renderer. So the personal graph hangs every fact off one "you" node (everything
there was *said*, not sensed), the world graph hangs every subject off the
source that observed it (the only relation the evidence actually records), and
`GET /api/v1/knowledge` returns both plus the sentence naming the boundary.

## Dashboard-first API

`GET /api/v1/world-memory` returns `{"claims": [...], "disputed": [...]}` with
the evidence attached to every entry. It is read-only: there is no POST, PUT or
DELETE, and no control endpoint of any kind. World memory is not
session-scoped — it describes the shared simulated world, not a person.

## Limits carried on purpose

- Confidence values come from the producing sensor adapter; they have not been
  calibrated against ground truth, so they order evidence rather than measure
  probability.
- The map layer has never run against a real SITL lidar stream end to end; it is
  proven by contract tests over recorded scan shapes, not by a live flight.
- The store is local JSON Lines. Multi-process writers are not coordinated.
