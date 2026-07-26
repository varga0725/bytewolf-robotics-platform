# Vision UVC live evidence — Hawkeye, 2026-07-25

The first end-to-end run of the Vision contract path against real camera hardware:
a USB (UVC) camera producing canonical artifacts that the Cognitive Runtime's
consumer plugin reads. Proof level: **app + live camera** — not SITL, not a flight.

## The chain that ran

```
Hawkeye USB camera (PC-CAM / UVC mode)
  -> UvcCameraSource            (brain/vision/uvc.py)      frame + sha256-bound JPEG payload
  -> UltralyticsYoloDetector    (yolo11n.pt, cached)       detections
  -> canonical_from_detection_result + vision_summary_to_canonical
  -> vision_summary v0.1 artifact                          var/vision/front-summary.json
  -> load_vision_summary        (brain/vision/canonical.py, consumer side, fail-closed)
  -> vision.summary capability  (Plugin SDK registry, apps/plugins/vision_summary.py)
```

## What was observed

- **Device**: `USB Camera VID:1539 PID:34322` (`UVC Camera VendorID_1539 ProductID_34322`).
  Before the camera was switched out of Masstorage into PC-CAM mode, macOS mounted
  it as `USB Storage` and no video device existed — that mode switch is a
  precondition, not an optional step.
- **Capture**: 1920x1080 JPEG frames, published continuously (`--frames 0`).
- **Detections**: real YOLO output varying live across samples — `book`, `person`,
  `kite`. Confidences were low (0.26-0.39) and `kite` is a plain misdetection:
  yolo11n on a wide-angle FPV lens is a plumbing-grade model, not a validated
  aerial detector. The detection *path* is proven; detector quality is not.
- **Freshness**: sampled through the consumer plugin, artifact age was 55-232 ms
  and `state` resolved `valid` on every sample against `max_age_s = 1.0`.
- **Consumer**: `vision.summary` invoked through the Plugin SDK registry returned
  `available=True`, the live detection identities, and `health=ok`.

## What this does not show

- No SITL, no flight, no actuation: the whole path is observation-only, and the
  Vision domain carries a static test forbidding a MAVSDK/PX4 import.
- No calibration: frames are published `uncalibrated`, so nothing here supports a
  metric or pose claim.
- No detector validation: no aerial validation set, no benchmark KPIs. A label in
  an artifact is evidence that the pipeline ran, not that the label is right.

## Reproducing

```
cd <vision worktree>
python -m brain.cli.vision_uvc_producer \
    --device-index 0 \
    --artifact var/vision/front-summary.json \
    --weights ~/.cache/bytewolf-vision/yolo11n.pt \
    --frames 0 --interval-s 0.3 --preview
```

`--preview` opens a live window rendering each published frame with its detections
drawn. Omit `--weights` to publish zero-detection frames, which proves the capture
and artifact path without trusting a model.

On macOS the capturing process needs camera permission. A process spawned by an
agent's shell tool does not inherit it: run the producer from a terminal that
holds the grant.
