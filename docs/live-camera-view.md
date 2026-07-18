# Live camera view on the dashboard

See the drone's camera on the dashboard while it flies in simulation, with any
detected object boxed over the frame. Everything stays inside the safety
architecture: the relay only reads the camera topic and writes files the
read-only dashboard serves. It sends nothing to PX4, opens no control path, and
emits no MAVLink.

## Run it

Start the simulator with a camera profile (down- or front-facing):

```zsh
./simulation/gazebo/launch/run_px4_gazebo_headless.zsh mono-down
```

Stream the camera to the dashboard's files (one terminal):

```zsh
.venv/bin/python -m simulation.perception.camera_stream --sensor down \
  --camera-file simulation/artifacts/dashboard/camera.png \
  --detections-file simulation/artifacts/dashboard/detections.json
```

Serve the dashboard with those files (another terminal):

```zsh
.venv/bin/python -m apps.dashboard.server \
  --telemetry-file simulation/artifacts/dashboard/live-telemetry.json \
  --camera-file simulation/artifacts/dashboard/camera.png \
  --detections-file simulation/artifacts/dashboard/detections.json
```

Open `http://127.0.0.1:8080`. Fly a mission from a third terminal (for example
`.venv/bin/python -m brain.cli.fly_takeoff_hover_land --altitude 8 --hover-seconds 30`);
the camera card updates about twice a second, and a red marker in view is drawn
as a labelled box.

## How it works

The simulator publishes raw RGB frames. The relay decodes each one into the
hardware-independent `CameraFrame`, runs it through the detector adapter, encodes
it to lossless PNG with the standard library alone -- no image dependency -- and
writes both the frame and the detections atomically, so the dashboard never reads
a half-written file. PNG keeps the picture lossless, so what the dashboard shows
is exactly what the detector saw; JPEG/MJPEG stays a streaming and UI concern,
never the basis of the pipeline.

The detector is replaceable. The default is the dependency-free colour-marker
backend, which boxes a bright object; a learned YOLO-compatible backend drops in
behind the same interface without touching the relay, the dashboard, or the
safety layer.
