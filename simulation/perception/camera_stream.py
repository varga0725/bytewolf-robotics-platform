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


def _gz_python_bindings():
    """Import Gazebo's own transport bindings, adding Homebrew's path if needed.

    Gazebo Harmonic ships Python bindings, but into Homebrew's site-packages
    rather than this project's virtualenv. Reaching them is the difference
    between 2 and 30 frames a second: the previous transport shelled out to
    `gz topic --json-output`, which base64-encodes every frame into JSON — at
    1080p that is 8 MB of text per frame through a pipe, parsed in Python.
    """
    import sys

    try:
        from gz.transport13 import Node
        from gz.msgs10.image_pb2 import Image
    except ImportError:
        candidate = f"/opt/homebrew/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        if candidate not in sys.path:
            sys.path.append(candidate)
        try:
            from gz.transport13 import Node
            from gz.msgs10.image_pb2 import Image
        except ImportError as error:  # pragma: no cover - environment guard
            raise CameraStreamError(
                "Gazebo's Python transport bindings are not importable. They ship with "
                "gz-transport (Homebrew: /opt/homebrew/lib/pythonX.Y/site-packages)."
            ) from error
    return Node, Image


class CameraStreamError(RuntimeError):
    """The camera cannot be read, so the dashboard shows nothing rather than something stale."""


def subscribe_camera_frames(
    topic: str,
    *,
    sensor_id: str,
    now: Callable[[], datetime],
    on_frame: Callable[[CameraFrame], None],
) -> object:
    """Subscribe read-only to a camera topic and hand each frame to `on_frame`.

    The work happens in Gazebo's own callback rather than being posted to a
    consumer thread. That is not a micro-optimisation: handing frames across a
    queue cost a third of them — 30 fps arriving became 19 fps published, with
    everything the loop actually does fitting in under 5 ms. There was never a
    shortage of time, only a handoff.

    Returns the node, which the caller must keep alive: dropping it tears the
    subscription down and the frames stop with nothing to explain why.
    """
    # Gazebo discovery needs this, and the old transport set it on the
    # subprocess environment. In-process it has to be set before the node
    # exists: without it the subscription succeeds and no frame ever arrives,
    # which looks exactly like a camera that is not publishing.
    os.environ.setdefault("GZ_IP", "127.0.0.1")
    Node, Image = _gz_python_bindings()

    def on_image(message) -> None:
        frame = camera_frame_from_gz_message(message, sensor_id=sensor_id, captured_at=now())
        if frame is not None:
            on_frame(frame)

    node = Node()
    if not node.subscribe(Image, topic, on_image):
        raise CameraStreamError(f"Could not subscribe to the camera topic {topic!r}.")
    return node


def camera_frame_from_gz_message(message, *, sensor_id: str, captured_at: datetime) -> CameraFrame | None:
    """Build an RGB8 frame from a gz.msgs.Image, or None when it is unusable."""
    width, height, data = int(message.width), int(message.height), bytes(message.data)
    if width <= 0 or height <= 0 or len(data) != width * height * 3:
        return None
    return CameraFrame(
        sensor_id=sensor_id, encoding=FrameEncoding.RGB8, width=width, height=height,
        data=data, captured_at=captured_at, frame_id=None,
    )


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


class DetectionWorker:
    """Run the detector beside the picture, never in front of it.

    One frame at a time and the newest wins: a detector that falls behind
    should skip frames, not build a backlog and publish boxes for a view the
    vehicle has already left. Detections carry their own capture time, so a
    late one is visibly late rather than quietly wrong.
    """

    def __init__(
        self,
        detector: DetectorAdapter,
        detections_path: Path,
        *,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        import threading

        self._detector = detector
        self._path = detections_path
        self._on_error = on_error
        self._pending: CameraFrame | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="detector", daemon=True)

    def start(self) -> DetectionWorker:
        self._thread.start()
        return self

    def submit(self, frame: CameraFrame) -> None:
        with self._lock:
            self._pending = frame
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
            if frame is None:
                continue
            try:
                document = self._detector.analyze(frame).to_document()
                _atomic_write_bytes(self._path, (json.dumps(document) + "\n").encode("utf-8"))
            except Exception as error:  # noqa: BLE001 - reported, never fatal to the picture
                if self._on_error is not None:
                    self._on_error(error)


def run_camera_stream(
    *,
    camera_topic: str,
    camera_path: Path,
    detections_path: Path,
    sensor_id: str,
    detector: DetectorAdapter,
    now: Callable[[], datetime] | None = None,
    period_s: float = 1 / 30,
    detect_period_s: float = 0.2,
    environment: dict[str, str] | None = None,
    should_continue: Callable[[], bool] | None = None,
    encode: Callable[[CameraFrame], bytes] = encode_frame_to_jpeg,
    subscribe: Callable[..., object] = subscribe_camera_frames,
) -> None:
    """Relay frames from the camera topic to the dashboard until stopped."""
    clock = now or (lambda: datetime.now(UTC))
    keep_going = should_continue or (lambda: True)
    camera_path.parent.mkdir(parents=True, exist_ok=True)
    detections_path.parent.mkdir(parents=True, exist_ok=True)

    schedule = _PublishSchedule(period_s=period_s, detect_period_s=detect_period_s)
    detection_worker = DetectionWorker(
        detector,
        detections_path,
        on_error=lambda error: print(f"Detector skipped a frame: {type(error).__name__}: {error}"),
    ).start()

    def publish(frame: CameraFrame) -> None:
        moment = time.monotonic()
        if not schedule.should_publish(moment):
            return
        _atomic_write_bytes(camera_path, encode(frame))
        # The detector is the expensive half - 44 ms a frame at 1080p, enough
        # to cap the picture on its own. It runs beside the stream, on its own
        # slower cadence, well inside the 0.5 s freshness the detections
        # contract declares.
        if schedule.should_detect(moment):
            detection_worker.submit(frame)

    node = None
    try:
        node = subscribe(
            camera_topic, sensor_id=sensor_id, now=clock, on_frame=publish
        )
        while keep_going():
            time.sleep(0.05)
    finally:
        detection_worker.stop()
        del node


class _PublishSchedule:
    """Decide, per frame, whether to publish it and whether to look at it.

    Kept apart from the work so both cadences are one readable decision each,
    and so a test can drive them without a simulator.
    """

    # A frame that arrives a hair early is still this frame's turn. Asking for
    # exactly the rate the camera publishes at otherwise beats against it: each
    # 33.0 ms frame missed a 33.3 ms deadline by a fraction, was dropped, and
    # 30 fps arriving became 19 fps published. The tolerance is what makes
    # "every frame" mean every frame.
    _EARLY_TOLERANCE = 0.25

    def __init__(self, *, period_s: float, detect_period_s: float) -> None:
        self._period_s = period_s
        self._detect_period_s = detect_period_s
        self._next_publish_at = 0.0
        self._next_detect_at = 0.0

    def should_publish(self, moment: float) -> bool:
        if moment < self._next_publish_at - self._period_s * self._EARLY_TOLERANCE:
            return False
        # Paced from now, not from the missed deadline, so a slow frame cannot
        # make the next few publish back to back trying to catch up.
        self._next_publish_at = moment + self._period_s
        return True

    def should_detect(self, moment: float) -> bool:
        if moment < self._next_detect_at:
            return False
        self._next_detect_at = moment + self._detect_period_s
        return True


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
    parser.add_argument(
        "--period-s", type=float, default=1 / 30,
        help="Seconds between published frames. The camera renders at 30 fps.",
    )
    parser.add_argument(
        "--detect-period-s", type=float, default=0.2,
        help="Seconds between detector runs. Kept inside the 0.5 s the detections contract allows.",
    )
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
        detect_period_s=parsed.detect_period_s,
            encode=FRAME_ENCODERS[parsed.format],
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
