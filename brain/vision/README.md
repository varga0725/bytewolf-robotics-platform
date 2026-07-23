# Vision Core (P0)

Runtime prerequisite: Python 3.11 or newer. The versioned contracts use
timezone-aware `datetime.UTC` timestamps and reject timezone-less values at
the ingest boundary.

Observation-only camera perception domain. It carries camera evidence,
detections, tracks, health and benchmark data; it must never import or emit
flight-control commands.

## P0 components

- `contracts.py` — immutable CameraFrame/DetectionResult/health contracts,
  freshness, clock-skew and anti-replay validation.
- `runtime.py` — dependency-injected recorded, Gazebo and GStreamer adapter
  boundaries; newest-frame-wins backpressure and explicit reconnect health.
- `evidence.py` — metadata-first evidence clips and safe local retention.
- `presentation.py` — atomic read-only dashboard artifacts.
- `benchmark.py` — deterministic latency and tracking KPI aggregation.

The concrete Gazebo transport, GStreamer pipeline and model weights remain
deployment adapters: they must feed the contracts through the narrow runtime
ports and cannot be imported by this domain.

Run the focused tests from the repository root:

```zsh
python3 -m unittest discover -s tests -p 'test_vision_*.py' -v
```

The P0 research baseline is **YOLO11n**. The recorded pipeline defaults to
`--detector yolo`; every YOLO invocation must supply an explicit, existing
local weights file (for example, an approved local `yolo11n.pt`). It must not
rely on implicit model downloads. `--detector annotations` remains available
only for deterministic recorded fixtures and tests, not as a deployment
baseline. Install the isolated research runtime first:

```zsh
python3 -m pip install -r requirements-vision-research.txt
```

Model selection is declared in
`shared/config/vision/models.v1.yaml`. Research records may be evaluated only
in non-public environments. A production/public record needs a documented
license reference and separately provisioned local weights; the config never
ships weights or grants permission to download them.

The dashboard can expose published local artifacts without gaining an ingest
or control route:

```zsh
python3 -m apps.dashboard.server \
  --telemetry-file /path/to/telemetry.json \
  --vision-status-file /path/to/vision-status.json \
  --vision-frame-file /path/to/vision-frame.jpg
```

Each recorded, GStreamer and Gazebo CLI also accepts `--metadata-path
/secure/local/vision-metadata.jsonl`. This is a single-runtime, append-only
local journal of the versioned dashboard read model and writer timestamp. It
never stores raw frames, payload hashes, evidence locations, face templates or
embeddings; use a separate P3 datastore for multi-writer operation.

For encrypted local evidence clips, use `FernetEvidenceWriter` with an explicit
`BYTEWOLF_VISION_EVIDENCE_KEY` Fernet key supplied by the deployment. The key
is never generated, persisted or logged by Vision. The writer creates
authenticated encrypted files with owner-only permissions; missing crypto
runtime or key fails closed.

`EvidenceCaptureBuffer` is the bounded in-memory bridge between hash-verified
frames and that writer. It writes no disk data until a caller explicitly
requests an `EvidenceEvent`, then waits for the configured post-event window
before encrypting only the selected pre/post clip. It is observation storage,
not an automatic recording or flight-control mechanism. Its `enforce_retention`
method deletes only its tracked encrypted records once the policy deadline is
reached.

Recorded benchmark runs can add `--benchmark-manifest-path benchmark.json`.
The manifest hash-binds the fixture, selected model configuration and (for
YOLO) local weights to the exported KPI report; it contains no frame payload,
embedding or control data.

## P1 foundation: opt-in face verification

`face_verification.py` currently defines only immutable `FaceVerification v1`
and `BiometricConsent` evidence. Subject IDs must be pseudonymous, consent is
explicit and revocable, and a liveness or quality failure cannot be represented
as a match. This contract stores neither embeddings nor templates, is excluded
from `vision_dashboard.v1`, and cannot authorize an action or affect flight.

`BiometricTemplateStore` is a private P1 persistence boundary for opt-in
enrollment only. It requires active matching consent at enrollment and load,
encrypts templates with a caller-provided Fernet key, uses hashed filenames and
owner-only files, forbids implicit replacement, and deletes only the matching
template after effective consent revocation. It is deliberately not exported
through the Vision package, dashboard, or metadata journal.

`FaceVerificationGate` is the private P1 multi-frame confirmation layer. It
uses an adapter-supplied scalar similarity only, requires configurable repeated
evidence, applies an acceptance/continuation hysteresis band and cooldown, and
returns fail-closed quality, liveness, consent or sequence outcomes as
`FaceVerification v1` evidence. It never accepts raw embeddings or issues an
authorization/control decision.

`FaceQualityGate` is the first P1 quality-gate implementation from the Vision
plan: a deterministic, versioned face-size, blur, lighting and pose check. It
consumes scalar adapter metrics rather than face pixels and returns an explicit
fail-closed reason before any private 1:1 similarity evaluation.

`PrivateFaceEmbedding` and `PrivateOneToOneVerifier` provide the next private
P1 seam: ArcFace-compatible 128/512-dimensional vectors can be serialized only
for the encrypted template store and compared only within the process. The
verification output is model provenance plus one cosine-similarity scalar; no
embedding or template is added to a public contract.

`ArcFaceOnnxEmbedder` is the explicit-local research adapter for a manually
provisioned 112×112 model. It requires both an existing local path and an
approved SHA-256, does not fetch models, converts one in-memory aligned BGR
crop to a private embedding, and is deliberately separate from face detection,
the dashboard and the generic P0 runtime.

`ScrfdFaceCandidate` and `align_five_point_bgr` form the adjacent private
research seam. A future explicit-local SCRFD adapter must provide exactly five
ordered landmarks (left eye, right eye, nose, left mouth, right mouth); the
utility validates them, rejects out-of-image or degenerate data, and makes only
an in-memory 112×112 BGR ArcFace crop. It neither selects an identity from a
multi-face result nor stores boxes, landmarks or crops. `SimilarityResult`
keeps the raw cosine value private to the process and exposes an explicit
`normalized_similarity` when constructing the confirmation gate's `[0, 1]`
observation.

`ScrfdOnnxDetector` now decodes the three-stride, nine-output SCRFD-10GF
layout directly through ONNX Runtime. It requires an explicit local model path
and an approved SHA-256 at construction; it never downloads or retains image
data. Only exactly one post-NMS, in-frame five-landmark candidate is returned.
Zero, malformed or multiple faces return `None`, so a future coordinator must
record an unavailable result rather than select an arbitrary identity. The
current research artifact is InsightFace `buffalo_l`'s `det_10g.onnx` and is
restricted to non-commercial research; it remains outside this repository and
cannot be promoted to a public or production model without separate licensing.

`FaceVerificationCoordinator` composes only already validated, fresh
`CameraFrame` evidence. Its order is consent → one SCRFD face → quality →
liveness → alignment → ArcFace → private 1:1 comparison → multiframe gate.
Every unavailable or unexpected stage fails closed, and its sole output is a
`FaceVerification v1` audit record; it is not exported by the Vision package
and cannot issue an authorization or flight-control decision.

## Cognitive Runtime boundary

The Vision domain is a perception capability producer. `DetectionEvent`,
`TrackedObject`, `VideoArtifactRef` and `VisionSummary` are immutable,
TTL-bound, observation-only contracts for a future read-only Cognitive Runtime
plugin. `canonical_from_detection_result()` is deliberately pure: it neither
publishes to the dashboard nor writes memory, sends an event, makes a proposal,
or reaches a Mission/PX4/MAVSDK interface. A Cognitive Hook may turn these
evidence records into a proposal only through its own schema-validation,
admission and policy path.
