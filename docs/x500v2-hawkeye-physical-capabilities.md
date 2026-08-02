# X500 V2 + Hawkeye — physical capability and run guide

This page states what the current repository can do with the real Holybro X500
V2 kit and the acquired Hawkeye 4K Split V5 camera. It deliberately separates
live-camera proof, PX4/Gazebo proof, and physical-flight proof.

## Safety status first

The physical airframe is **not cleared for autonomous flight** by this
repository today. The twin's mass, thrust curve, braking, drag, sensor latency
and camera calibration are still `provenance: simulated`; a flight battery and
physical ULog/bench measurements are also outstanding.

Do not connect any code path in this repository to a live propeller system
without a controlled physical-flight test plan, a suitable battery, legal
airspace, a safety pilot, and a separately reviewed hardware bring-up.

## Hardware assumed by this guide

- Holybro X500 V2 PX4 development kit: Pixhawk 6C, M8N GPS, motors and ESCs.
- Hawkeye 4K Split V5 in **PC-CAM / USB UVC** mode.
- A development host running this repository. The Hawkeye is a sensor; it is
  not an onboard computer and cannot run the perception or safety software by
  itself.

The camera's observed device identity is `USB Camera VID:1539 PID:34322`.
macOS must grant the terminal application camera permission.

## What works with only the Hawkeye as an added sensor

| Capability | Proof level | Status |
| --- | --- | --- |
| Acquire live UVC frames and preview them | app + live camera | works |
| Produce signed frame artifacts and `VisionSummary` | app + live camera | works |
| Feed read-only camera evidence to the Cognitive Runtime | app + live camera | works |
| Record camera evidence for later review | app + live camera | works |
| Semantic detection/tracking when a configured backend is available | contract / host-dependent | available, not flight authority |
| Physical takeoff, waypoint flight, RTL or autonomous obstacle avoidance | physical flight | not validated |

The Hawkeye is RGB/FPV imagery. It may provide semantic evidence such as a
person, marker, or scene event, but RGB absence never proves obstacle-free
space. It cannot be used as a ranging sensor, landing-clearance proof, or
30 km/h obstacle-avoidance authority without a calibrated metric depth/stereo
source and a separately proven fusion path.

## Live camera run, without flying

From the repository root:

```bash
.venv/bin/python -m brain.cli.vision_uvc_producer \
  --device-index 0 \
  --device-id hawkeye-usb \
  --camera-id front_rgb \
  --preview
```

This opens the UVC feed, validates frames, and publishes canonical vision
artifacts. Stop it with `Ctrl-C`. If camera index zero is not Hawkeye, inspect
the macOS camera list and pass the correct index; do not guess from a stale
preview.

For evidence recording rather than a live preview, use the corresponding
recorder:

```bash
.venv/bin/python -m brain.cli.vision_uvc_recorder \
  --device-index 0 \
  --device-id hawkeye-usb \
  --camera-id front_rgb \
  --preview
```

## What can be demonstrated safely today

1. Hawkeye capture, preview and artifact creation on the development host.
2. Camera evidence flowing through the observation-only Vision contract into
   the Cognitive Runtime and dashboard.
3. PX4/Gazebo flights and camera pipelines in simulation, with a visible GUI.
4. Mission planning, approval, audit, replay and deterministic safety logic
   without commanding the physical aircraft.

## What is intentionally blocked

- Physical autonomous flight: no measured X500 dynamics or physical replay.
- Physical obstacle avoidance: Hawkeye RGB is not metric obstacle ranging.
- 30 km/h physical cruise: current high-speed gate requires a trustworthy
  forward stopping horizon; the existing 30 m lidar profile is insufficient
  for the current simulated braking model, and Hawkeye RGB cannot extend it.
- VIO/SLAM from Hawkeye: calibration and a physical validation dataset are
  missing. The lens is fisheye; Gazebo's pinhole representation is only an
  approximation.

## Before the first physical flight

1. Install and validate the selected flight battery; confirm power, motor
   direction, propellers, GPS and PX4 failsafes with props removed where
   appropriate.
2. Calibrate the Hawkeye intrinsics and fisheye distortion over USB/UVC and
   record a valid camera-calibration contract.
3. Record physical ULogs for hover, braking, yaw and lateral response; use
   them to replace simulated limits.
4. Start with manual/safety-pilot flight and telemetry-only observation.
5. Progress only through offline replay → shadow → bounded physical test.

## Related sources

- `docs/vision-uvc-live-evidence.md` — existing Hawkeye live-camera evidence.
- `docs/hardware-bringup-x500v2.md` — hardware bring-up constraints.
- `docs/offboard-sitl-evidence.md` and `docs/shield-sitl-evidence.md` — SITL
  evidence only, not physical-flight approval.
- `shared/config/x500v2/twin.yaml` — canonical hardware and safety contract.
