"""Stream the simulator's camera to the read-only dashboard, live.

This is the wow moment: while the drone flies in Gazebo, its camera frames land
on the dashboard as a live picture, with any detected object boxed over it. It
stays inside the safety architecture -- the relay only reads the camera topic and
writes files the read-only dashboard serves; it sends nothing to PX4, opens no
control path, and emits no MAVLink.

Each frame is decoded from the sim's raw RGB, run through the detector, encoded
to lossless PNG, and written atomically -- to a temporary file that is then
renamed -- so the dashboard never reads a half-written frame. A frame that cannot
be decoded is skipped rather than shown, and a run with no detector still streams
the picture with an empty overlay.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import time

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.png_encoder import encode_frame_to_png


DOWN_CAMERA_TOPIC = "/world/default/model/x500_mono_cam_down_0/link/camera_link/sensor/imager/image"
FRONT_CAMERA_TOPIC = "/world/default/model/x500_mono_cam_0/link/camera_link/sensor/imager/image"
# A bright-red marker, the same the ground-truth scenario places; flying over one
# shows a live detection box on the dashboard.
DEFAULT_MARKER = ColourTarget(red=220, green=20, blue=20, tolerance=70)


def camera_frame_from_gz_image(message: dict, *, sensor_id: str, captured_at: datetime) -> CameraFrame | None:
    """Decode a gz camera image message into an RGB8 frame, or None if malformed."""
    try:
        width = int(message["width"])
        height = int(message["height"])
        data = base64.b64decode(message["data"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or len(data) != width * height * 3:
        return None
    return CameraFrame(
        sensor_id=sensor_id, encoding=FrameEncoding.RGB8, width=width, height=height,
        data=data, captured_at=captured_at, frame_id=None,
    )


def publish_frame(
    frame: CameraFrame,
    *,
    camera_path: Path,
    detections_path: Path,
    detector: DetectorAdapter,
    now: Callable[[], datetime],
) -> dict:
    """Encode the frame to PNG and write it and its detections for the dashboard."""
    _atomic_write_bytes(camera_path, encode_frame_to_png(frame))
    document = detector.analyze(frame).to_document()
    _atomic_write_bytes(detections_path, (json.dumps(document) + "\n").encode("utf-8"))
    return document


def run_camera_stream(
    *,
    camera_topic: str,
    camera_path: Path,
    detections_path: Path,
    sensor_id: str,
    detector: DetectorAdapter,
    now: Callable[[], datetime] | None = None,
    period_s: float = 0.5,
    environment: dict[str, str] | None = None,
    should_continue: Callable[[], bool] | None = None,
) -> None:
    """Relay frames from the camera topic to the dashboard until stopped."""
    clock = now or (lambda: datetime.now(UTC))
    env = environment or {**os.environ, "GZ_IP": "127.0.0.1"}
    keep_going = should_continue or (lambda: True)
    camera_path.parent.mkdir(parents=True, exist_ok=True)
    detections_path.parent.mkdir(parents=True, exist_ok=True)

    while keep_going():
        message = _capture_image(camera_topic, env)
        frame = None if message is None else camera_frame_from_gz_image(
            message, sensor_id=sensor_id, captured_at=clock()
        )
        if frame is not None:
            publish_frame(
                frame, camera_path=camera_path, detections_path=detections_path,
                detector=detector, now=clock,
            )
        time.sleep(period_s)


def _capture_image(topic: str, environment: dict[str, str]) -> dict | None:
    try:
        completed = subprocess.run(
            ("gz", "topic", "-e", "-t", topic, "--json-output", "-n", "1"),
            capture_output=True, text=True, timeout=10.0, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def main(arguments: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream the simulator camera to the read-only dashboard.")
    parser.add_argument("--sensor", choices=("down", "front"), default="down")
    parser.add_argument("--camera-file", type=Path, default=Path("simulation/artifacts/dashboard/camera.png"))
    parser.add_argument(
        "--detections-file", type=Path, default=Path("simulation/artifacts/dashboard/detections.json")
    )
    parser.add_argument("--period-s", type=float, default=0.5)
    parsed = parser.parse_args(arguments)

    topic = DOWN_CAMERA_TOPIC if parsed.sensor == "down" else FRONT_CAMERA_TOPIC
    sensor_id = "down_rgb" if parsed.sensor == "down" else "front_rgb"
    detector = DetectorAdapter(ColourMarkerBackend(DEFAULT_MARKER, label="marker"), source=f"gz {sensor_id}")
    print(f"Streaming {parsed.sensor} camera to {parsed.camera_file} (Ctrl-C to stop)")
    try:
        run_camera_stream(
            camera_topic=topic, camera_path=parsed.camera_file, detections_path=parsed.detections_file,
            sensor_id=sensor_id, detector=detector, period_s=parsed.period_s,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
