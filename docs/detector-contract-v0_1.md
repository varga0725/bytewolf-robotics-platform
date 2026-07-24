# Camera detector contract and adapter

`brain/perception/detector.py` is the vision half of the perception path. It
turns a camera frame into a versioned, validated detection result and does
nothing else — it emits data, never a command. Whatever it finds is a proposal;
the safety layer decides what, if anything, to do with it. The module imports
neither MAVSDK nor any flight adapter, enforced by a test.

## Four states a consumer must tell apart

A `DetectionResult` resolves to exactly one, and only one may be acted on:

| State | Meaning | Carries detections? |
| --- | --- | --- |
| `VALID` | the detector ran on a fresh frame and trusts it | yes (possibly empty) |
| `INVALID` | it ran but the result cannot be trusted — unreadable frame, or the backend raised | no |
| `MISSING` | no frame was available | no |
| `STALE` | trustworthy when captured, but older than its own `max_age_s` | refused |

A detector failure becomes an explicit `INVALID` result instead of an exception
a caller might read as "nothing detected", and staleness is derived from the
capture time, so a slow pipeline cannot pass an old frame off as current. An
invalid or missing result carries no detections at all, so absence can never be
read as "nothing there".

## The backend is replaceable

`DetectorBackend` is a one-method interface — a frame in, detections out. A
deterministic `StubDetectorBackend` ships here so the whole path is testable
without model weights: it returns the detections registered for a frame id, and
nothing for an unknown frame, making "object present" and "object absent" both
exact and repeatable. A real YOLO-compatible backend replaces it without
touching MissionSpec, the safety kernel, or this adapter's contract.

## The adapter validates its own output

The adapter checks every result against `detection_v0_1.schema.json` and against
the frame bounds before returning it, so a backend that emits an off-frame box
or an over-confident score fails closed to `INVALID` rather than reaching a
consumer. Confidence stays in [0, 1]; boxes stay inside the frame.

## Shape

The result document is what the read-only dashboard consumes on `/api/detections`:

```json
{
  "contract_version": "v0.1",
  "captured_at": "2026-07-18T09:00:00Z",
  "max_age_s": 0.5,
  "validity": "valid",
  "frame": {"width": 640, "height": 480, "frame_id": "frame-1"},
  "detections": [
    {"label": "landing-pad", "confidence": 0.92, "bbox": {"x": 120, "y": 80, "width": 200, "height": 150}}
  ]
}
```

## Scope and what remains

This delivers the camera-frame representation, the detection contract, the
adapter, and a deterministic backend, tested end to end with synthetic frames.
Grounding the frame source against a real `gz_x500_mono_cam` SITL capture — and
the encoding from Gazebo's raw image to the JPEG the dashboard serves — is the
next slice; the adapter already accepts any frame, so that work does not change
this contract.
