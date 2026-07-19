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
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import time

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.jpeg_encoder import encode_frame_to_jpeg
from brain.perception.png_encoder import encode_frame_to_png


# The live view defaults to JPEG: at 1080p it is an order of magnitude smaller
# than lossless PNG, which is what keeps the stream smooth. PNG stays available
# for when an exact, lossless frame is wanted.
FRAME_ENCODERS: dict[str, Callable[[CameraFrame], bytes]] = {
    "jpeg": encode_frame_to_jpeg,
    "png": encode_frame_to_png,
}


# Baylands is the platform's default world.
DOWN_CAMERA_TOPIC = "/world/baylands/model/x500_mono_cam_down_0/link/camera_link/sensor/imager/image"
FRONT_CAMERA_TOPIC = "/world/baylands/model/x500_mono_cam_0/link/camera_link/sensor/imager/image"
FULL_DOWN_CAMERA_TOPIC = "/world/baylands/model/x500_mono_cam_down_0/link/down_camera_link/sensor/down_imager/image"
FULL_FRONT_CAMERA_TOPIC = "/world/baylands/model/x500_mono_cam_down_0/link/front_camera_link/sensor/front_imager/image"
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


def camera_topic(sensor: str, *, full_sensors: bool = False) -> str:
    """Return the exact Gazebo topic for a selected physical camera."""
    if sensor not in {"down", "front"}:
        raise ValueError(f"Unknown camera sensor: {sensor}")
    if full_sensors:
        return FULL_DOWN_CAMERA_TOPIC if sensor == "down" else FULL_FRONT_CAMERA_TOPIC
    return DOWN_CAMERA_TOPIC if sensor == "down" else FRONT_CAMERA_TOPIC


def publish_frame(
    frame: CameraFrame,
    *,
    camera_path: Path,
    detections_path: Path,
    detector: DetectorAdapter,
    now: Callable[[], datetime],
    encode: Callable[[CameraFrame], bytes] = encode_frame_to_jpeg,
) -> dict:
    """Encode the frame and write it and its detections for the dashboard."""
    _atomic_write_bytes(camera_path, encode(frame))
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
    encode: Callable[[CameraFrame], bytes] = encode_frame_to_jpeg,
) -> None:
    """Relay frames from the camera topic to the dashboard until stopped."""
    clock = now or (lambda: datetime.now(UTC))
    env = environment or {**os.environ, "GZ_IP": "127.0.0.1"}
    keep_going = should_continue or (lambda: True)
    camera_path.parent.mkdir(parents=True, exist_ok=True)
    detections_path.parent.mkdir(parents=True, exist_ok=True)

    next_publish_at = 0.0
    while keep_going():
        received = False
        for message in _stream_gz_images(camera_topic, env):
            received = True
            if not keep_going():
                return
            if time.monotonic() < next_publish_at:
                continue
            frame = camera_frame_from_gz_image(message, sensor_id=sensor_id, captured_at=clock())
            if frame is None:
                continue
            publish_frame(
                frame, camera_path=camera_path, detections_path=detections_path,
                detector=detector, now=clock, encode=encode,
            )
            next_publish_at = time.monotonic() + period_s
        # Gazebo may restart while the dashboard stays open. Retry the read-only
        # subscription without busy-looping until its topic returns.
        if keep_going() and not received:
            time.sleep(min(period_s, 1.0))


def _stream_gz_images(
    topic: str,
    environment: dict[str, str],
    *,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> Iterator[dict]:
    """Yield camera messages from one persistent, read-only Gazebo subscription."""
    try:
        process = popen(
            ("gz", "topic", "-e", "-t", topic, "--json-output"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
        )
    except OSError:
        return
    try:
        if process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                yield message
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    return None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def main(arguments: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream the simulator camera to the read-only dashboard.")
    parser.add_argument("--sensor", choices=("down", "front"), default="down")
    parser.add_argument("--full-sensors", action="store_true", help="Use the dual-camera + LiDAR X500 profile.")
    parser.add_argument("--format", choices=tuple(FRAME_ENCODERS), default="jpeg")
    parser.add_argument("--camera-file", type=Path, default=None)
    parser.add_argument(
        "--detections-file", type=Path, default=Path("simulation/artifacts/dashboard/detections.json")
    )
    parser.add_argument("--period-s", type=float, default=0.5)
    parsed = parser.parse_args(arguments)

    topic = camera_topic(parsed.sensor, full_sensors=parsed.full_sensors)
    sensor_id = "down_rgb" if parsed.sensor == "down" else "front_rgb"
    suffix = "jpg" if parsed.format == "jpeg" else "png"
    camera_path = parsed.camera_file or Path(f"simulation/artifacts/dashboard/camera.{suffix}")
    detector = DetectorAdapter(ColourMarkerBackend(DEFAULT_MARKER, label="marker"), source=f"gz {sensor_id}")
    print(f"Streaming {parsed.sensor} camera ({parsed.format}) to {camera_path} (Ctrl-C to stop)")
    try:
        run_camera_stream(
            camera_topic=topic, camera_path=camera_path, detections_path=parsed.detections_file,
            sensor_id=sensor_id, detector=detector, period_s=parsed.period_s,
            encode=FRAME_ENCODERS[parsed.format],
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
