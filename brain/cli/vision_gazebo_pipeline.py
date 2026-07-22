"""Run the read-only P0 YOLO pipeline from a Gazebo RGB image topic.

Gazebo transport is a host concern confined to this composition root.  Frames
are first accepted by the immutable Vision ingest contract, then the exact
hash-bound RGB bytes are decoded for YOLO and rendered as a dashboard overlay.
No flight-control package or command interface is imported or exposed here.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Protocol, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brain.vision.benchmark import BenchmarkAggregator, BenchmarkSample
from brain.vision.gazebo import GazeboImageIngestAdapter, GazeboImageValidationError, GazeboSubscriptionBindings
from brain.vision.overlay import render_jpeg_overlay
from brain.vision.presentation import VisionArtifactPublisher
from brain.vision.runtime import DetectorPort, RuntimeState, VisionRuntime
from brain.vision.tracking import IoUAssociationTracker
from brain.vision.ultralytics import UltralyticsYoloDetector


class _ImageDetails(Protocol):
    data: bytes
    width: int
    height: int


class _CapturingBindings:
    """Capture only a callback's current payload before contract validation."""

    def __init__(self, bindings: GazeboSubscriptionBindings, capture: Callable[[object], None]) -> None:
        self._bindings = bindings
        self._capture = capture

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> Callable[[], None]:
        def observed(message: object) -> None:
            self._capture(message)
            callback(message)

        return self._bindings.subscribe(topic, observed)


class HashVerifiedGazeboSource:
    """Expose only Gazebo RGB bytes whose SHA-256 matches an accepted frame."""

    def __init__(
        self,
        *,
        bindings: GazeboSubscriptionBindings,
        topic: str,
        device_id: str,
        camera_id: str,
        calibration_version: str,
        clock: Callable[[], datetime],
        session_id_factory: Callable[[], str],
    ) -> None:
        self._payloads: dict[str, bytes] = {}
        self._shapes: dict[str, tuple[int, int]] = {}
        self._latest_payload: bytes | None = None
        self._latest_shape: tuple[int, int] | None = None
        self._clock = clock
        self.adapter = GazeboImageIngestAdapter(
            bindings=_CapturingBindings(bindings, self._capture), topic=topic,
            device_id=device_id, camera_id=camera_id, calibration_version=calibration_version,
            clock=clock, session_id_factory=session_id_factory,
        )

    def start(self) -> None:
        self.adapter.start()

    def close(self) -> None:
        self.adapter.stop()

    @property
    def stream_session_id(self) -> str:
        return self.adapter.lifecycle(self._clock()).stream_session_id

    def poll(self):  # type: ignore[no-untyped-def]
        lifecycle = self.adapter.lifecycle(self._clock())
        if lifecycle.stream_state == "degraded":
            raise GazeboImageValidationError(lifecycle.reason or "Gazebo image was rejected")
        if lifecycle.stream_state == "unavailable":
            raise RuntimeError(lifecycle.reason or "Gazebo stream is unavailable")
        frame = self.adapter.poll()
        if frame is None:
            return None
        payload, shape = self._latest_payload, self._latest_shape
        if payload is None or shape is None or hashlib.sha256(payload).hexdigest() != frame.payload_hash:
            raise ValueError("Gazebo host did not retain the accepted hash-bound RGB payload.")
        existing = self._payloads.setdefault(frame.payload_hash, payload)
        if existing != payload or self._shapes.setdefault(frame.payload_hash, shape) != shape:
            raise ValueError("Gazebo payload hash collision with conflicting image evidence.")
        return frame

    def resolve(self, payload_hash: str) -> bytes:
        try:
            return self._payloads[payload_hash]
        except KeyError as error:
            raise ValueError("No hash-verified Gazebo payload is available for inference.") from error

    def decode_rgb(self, payload: bytes) -> Any:
        """Decode the exact hash-bound RGB bytes for Ultralytics, never JPEG-transcode inference input."""
        payload_hash = hashlib.sha256(payload).hexdigest()
        if self.resolve(payload_hash) != payload:
            raise ValueError("Gazebo decoder received unregistered RGB evidence.")
        try:
            import cv2
            import numpy
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError("OpenCV and NumPy are required for the YOLO Gazebo adapter.") from error
        width, height = self._shapes[payload_hash]
        image = numpy.frombuffer(payload, dtype=numpy.uint8).reshape((height, width, 3))
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    def dashboard_jpeg(self, payload_hash: str) -> bytes:
        try:
            import cv2
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError("OpenCV is required for the Gazebo dashboard renderer.") from error
        encoded, jpeg = cv2.imencode(".jpg", self.decode_rgb(self.resolve(payload_hash)))
        if not encoded:
            raise RuntimeError("OpenCV could not encode the Gazebo dashboard image.")
        return bytes(jpeg)

    def reconnect(self, observed_at: datetime) -> None:
        previous_session = self.stream_session_id
        self.adapter.reconnect(observed_at)
        if self.stream_session_id == previous_session:
            self.adapter.disconnect("reconnect rejected: stream session was not rotated", observed_at)
            raise ValueError("Gazebo reconnect must rotate the stream session ID.")

    def _capture(self, message: object) -> None:
        """Best-effort capture; GazeboImageIngestAdapter remains the authority."""
        try:
            image = message  # keep malformed host objects out of the payload map
            payload = bytes(getattr(image, "data"))
            width, height = int(getattr(image, "width")), int(getattr(image, "height"))
            if width <= 0 or height <= 0:
                return
        except (AttributeError, TypeError, ValueError):
            return
        self._latest_payload = payload
        self._latest_shape = (width, height)


class GzTransportBindings:
    """Lazy Gazebo transport binding; required only on a simulator host."""

    def __init__(self) -> None:
        try:
            from gz.transport13 import Node
        except ImportError as error:  # pragma: no cover - host guard
            raise RuntimeError("Gazebo Python transport bindings are unavailable on this host.") from error
        try:
            # Gazebo Transport 13 ships its generated Python messages in the
            # versioned ``gz.msgs10`` namespace.  Some packaged hosts expose
            # an unversioned compatibility namespace, so retain that fallback
            # without altering the transport call shape.
            from gz.msgs10.image_pb2 import Image
        except ImportError:  # pragma: no cover - package compatibility guard
            try:
                from gz.msgs.image_pb2 import Image
            except ImportError as error:  # pragma: no cover - host guard
                raise RuntimeError("Gazebo Image message bindings are unavailable on this host.") from error
        self._node = Node()
        self._image_type = Image

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> Callable[[], None]:
        if not self._node.subscribe(self._image_type, topic, callback):
            raise RuntimeError(f"Gazebo could not subscribe to image topic {topic!r}.")

        def unsubscribe() -> None:
            if not self._node.unsubscribe(topic):
                raise RuntimeError(f"Gazebo could not unsubscribe from image topic {topic!r}.")

        return unsubscribe


def run_gazebo_pipeline(
    source: HashVerifiedGazeboSource,
    detector: DetectorPort,
    *,
    status_path: Path,
    frame_path: Path,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    max_iterations: int,
    idle_sleep_seconds: float = 0.05,
    max_reconnects: int = 3,
) -> dict[str, object]:
    """Process bounded Gazebo observations and publish only read-only evidence."""
    if type(max_iterations) is not int or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer.")
    if type(max_reconnects) is not int or max_reconnects < 0:
        raise ValueError("max_reconnects must be a non-negative integer.")
    if not isinstance(idle_sleep_seconds, (int, float)) or idle_sleep_seconds < 0:
        raise ValueError("idle_sleep_seconds must be non-negative.")
    runtime = VisionRuntime(detector, tracker=IoUAssociationTracker())
    publisher = VisionArtifactPublisher(status_path, frame_path)
    processed = unavailable = idle = reconnects = 0
    samples: list[BenchmarkSample] = []
    previous_source_drops = 0
    last_jpeg: bytes | None = None

    for _ in range(max_iterations):
        observed_at = now()
        accepted = runtime.ingest_once(source)
        outcome = runtime.process_next(observed_at)
        if outcome.state is RuntimeState.PROCESSED:
            assert outcome.frame is not None and outcome.detection is not None
            processed += 1
            last_jpeg = source.dashboard_jpeg(outcome.frame.payload_hash)
            source_drops = outcome.frame.dropped_frames
            samples.append(BenchmarkSample(outcome.frame.latency_ms, dropped_frames=max(0, source_drops - previous_source_drops)))
            previous_source_drops = source_drops
            publisher.publish(outcome.detection, outcome.health, now=observed_at, render=lambda result: render_jpeg_overlay(last_jpeg, result))
        elif outcome.state is RuntimeState.UNAVAILABLE:
            unavailable += 1
            publisher.publish(None, outcome.health, now=observed_at, render=lambda _result: last_jpeg or _unavailable_jpeg())
            last_jpeg = None
            if reconnects >= max_reconnects:
                break
            try:
                source.reconnect(observed_at)
            except Exception:
                break
            runtime.reconnect(observed_at)
            previous_source_drops = 0
            reconnects += 1
        elif outcome.state is RuntimeState.IDLE:
            idle += 1
            if not accepted:
                sleep(float(idle_sleep_seconds))

    benchmark = BenchmarkAggregator("gazebo-live").aggregate(samples) if samples else None
    return {
        "contract_version": "vision_gazebo_pipeline.v1",
        "model_id": detector.model_id,
        "model_version": detector.model_version,
        "processed_frames": processed,
        "unavailable_frames": unavailable,
        "idle_polls": idle,
        "reconnects": reconnects,
        "stream_session_id": source.stream_session_id,
        "benchmark": None if benchmark is None else {
            "sample_count": benchmark.sample_count,
            "p50_latency_ms": benchmark.p50_latency_ms,
            "p95_latency_ms": benchmark.p95_latency_ms,
            "dropped_frames": benchmark.dropped_frames,
        },
    }


def _unavailable_jpeg() -> bytes:
    try:
        import cv2
        import numpy
    except ImportError as error:  # pragma: no cover - deployment guard
        raise RuntimeError("OpenCV and NumPy are required for the Vision dashboard.") from error
    encoded, jpeg = cv2.imencode(".jpg", numpy.zeros((1, 1, 3), dtype=numpy.uint8))
    if not encoded:
        raise RuntimeError("OpenCV could not render unavailable Vision evidence.")
    return bytes(jpeg)


def _approved_weights_path(weights_path: Path) -> Path:
    if not weights_path.is_file():
        raise ValueError("YOLO requires an existing local --weights file; downloads are disabled.")
    return weights_path.resolve()


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only local YOLO Vision pipeline from a Gazebo RGB topic.")
    parser.add_argument("--topic", required=True, help="Gazebo RGB image topic")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--stream-session-id", required=True)
    parser.add_argument("--calibration-version", required=True)
    parser.add_argument("--weights", type=Path, required=True, help="Existing local YOLO weights; implicit downloads are disabled")
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--frame-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--idle-sleep", type=float, default=0.05)
    parser.add_argument("--max-iterations", type=int, default=1000)
    parser.add_argument("--max-reconnects", type=int, default=3)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_arguments(arguments)
    weights = _approved_weights_path(args.weights)
    session_number = 0

    def next_session_id() -> str:
        nonlocal session_number
        value = args.stream_session_id if session_number == 0 else f"{args.stream_session_id}-reconnect-{session_number}"
        session_number += 1
        return value

    source = HashVerifiedGazeboSource(
        bindings=GzTransportBindings(), topic=args.topic, device_id=args.device_id, camera_id=args.camera_id,
        calibration_version=args.calibration_version, clock=lambda: datetime.now(UTC), session_id_factory=next_session_id,
    )
    source.start()
    try:
        detector = UltralyticsYoloDetector(
            "research-yolo11n", weights.name, source, weights_path=str(weights), decoder=source.decode_rgb,
        )
        report = run_gazebo_pipeline(
            source, detector, status_path=args.status_path, frame_path=args.frame_path,
            now=lambda: datetime.now(UTC), sleep=time.sleep, max_iterations=args.max_iterations,
            idle_sleep_seconds=args.idle_sleep, max_reconnects=args.max_reconnects,
        )
    finally:
        source.close()
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.report_path is None:
        print(serialized)
    else:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    main()
