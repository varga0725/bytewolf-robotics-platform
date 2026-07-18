# Perception architecture

ByteWolf is a general, talking, autonomous embodied system in a drone body, not
a single-camera demo. The camera and every other sensor are inputs to one shared
perception stack, so the architecture is designed for that from the start:
multi-sensor, hardware-independent, and not tied to any one detection use case.

The first provable V1 capability is **visual target detection → relative
position estimation → mission reaction**. It may start with a landing pad or an
ArUco/AprilTag marker, because those are measurable and testable, but the
contracts below assume general object detection, navigation, obstacle sensing,
and later scene understanding — not a marker-only pipeline.

## Principles locked

1. **Multi-sensor, not single-camera.** The interfaces never assume one camera.
   The planned sensor set is a front RGB camera, a down camera, a depth or stereo
   camera, and a lidar or rangefinder. None is selected as hardware yet; the
   contracts must already carry more than one.
2. **Hardware-independent compute.** Development runs on a Mac today; the
   physical drone may later use a Raspberry Pi 5, an AI accelerator, or a Jetson.
   All of it sits behind the same adapter boundary, so the software is not bound
   to one compute platform.
3. **Format-agnostic pipeline.** The internal pipeline is not MJPEG-specific. One
   `CameraFrame` contract carries raw RGB/YUV, grayscale, depth, and compressed
   frames alike. JPEG/MJPEG is a streaming and UI convenience, never the basis of
   the vision architecture.
4. **Perception proposes; safety decides.** Every perception output is data, not
   a command. It reaches the mission logic only through the fail-closed states
   the observation contracts already enforce, and the Safety Kernel remains the
   authority. No detector, estimator, or learned policy writes an actuator topic.

## The frame contract

`brain/perception/camera_frame.py` is the hardware-independent frame every sensor
speaks. A `CameraFrame` names its `sensor_id` (`front_rgb`, `down_rgb`, `depth`,
…) and the `FrameEncoding` of its bytes, grouped by kind rather than by vendor:

| Kind | Encodings |
| --- | --- |
| Raw colour | `rgb8`, `bgr8`, `mono8`, `yuv422` |
| Depth | `depth16` (mm), `depth32f` (m) |
| Compressed | `jpeg` |

A raw frame must carry exactly `width × height × bytes-per-pixel` bytes, and the
contract refuses one that does not — a short buffer for the claimed dimensions is
not a picture of anything, and a malformed frame reaching a detector is how a
pipeline silently starts seeing things that are not there. A compressed frame's
length is opaque and only checked for presence. The same shape comes off a Gazebo
camera today and a real camera later, through the same boundary.

## The V1 flow (design)

```text
CameraFrame(sensor_id, encoding, …)
        │
        ▼
Detector adapter  ── replaceable backend (stub now, YOLO-compatible later)
        │            emits a validated DetectionResult; fail-closed on
        │            stale / failure / malformed
        ▼
Relative position estimator  ── detection + intrinsics + altitude/attitude
        │                       → target bearing and range, or a local
        │                       north/east/down offset, with uncertainty
        ▼
Target observation  ── a versioned, fail-closed observation the mission logic
        │               may act on only when VALID and fresh
        ▼
Mission reaction  ── WAIT_FOR_DETECTION → GOTO the target → precision approach,
                     always through the Safety Kernel, never a direct command
```

Stages one and two of this — the frame contract and the detector adapter with
its detection contract — are built and tested. The relative-position estimator
and the target observation are the next contracts to design and build; they slot
into the existing observation-contract discipline (valid / invalid / missing /
stale), so a target the vehicle cannot trust cannot drive a mission reaction.

## Simulation frame profiles (plan)

Because the pipeline is format-agnostic, the digital twin will exercise it under
separate, named frame profiles rather than one ideal stream:

- raw frame;
- JPEG/MJPEG compression;
- added latency;
- frame drop;
- sensor noise;
- motion blur;
- reduced or uneven FPS.

Each is a twin condition with its own evidence, the same way the wind and fault
profiles are — so "the detector works" is always qualified by the frame
conditions it was shown.

## Where the concrete camera lives

No camera model is chosen, deliberately: the system plan keeps the front camera
out of the V0 hardware baseline and records final peripherals as versioned
payload profiles when selected (part number, mass, mounting, power, data link,
rate, latency, noise model, Gazebo sensor model, integration). The contracts here
are the replaceable module that plan calls for; the concrete sensor drops in
behind them without changing the perception architecture.

Note: the shipped `gz_x500_mono_cam` produces 1280×960 raw RGB, while
`twin.yaml`'s documented `camera_front` intends 1280×720 — a drift to reconcile
when the front-camera profile is recorded.
