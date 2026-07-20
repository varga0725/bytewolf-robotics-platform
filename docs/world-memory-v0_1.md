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

## Dashboard-first API

`GET /api/v1/world-memory` returns `{"claims": [...], "disputed": [...]}` with
the evidence attached to every entry. It is read-only: there is no POST, PUT or
DELETE, and no control endpoint of any kind. World memory is not
session-scoped — it describes the shared simulated world, not a person.

## Limits carried on purpose

- Confidence values come from the producing sensor adapter; they have not been
  calibrated against ground truth, so they order evidence rather than measure
  probability.
- `map_region` is defined in the contract but has no producer yet; the world map
  layer is still to come.
- The store is local JSON Lines. Multi-process writers are not coordinated.
