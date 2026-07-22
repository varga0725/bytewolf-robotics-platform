"""Run an observation-only YOLO pipeline from a locally hosted GStreamer appsink.

The GStreamer host binding exists only in this CLI module.  The Vision domain
receives immutable, hash-bound frames and returns dashboard evidence only; this
command does not import or expose any flight-control capability.
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
from typing import Protocol, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brain.vision.gstreamer import AppSinkBuffer, AppSinkPipeline, GStreamerIngestAdapter, StreamBinding
from brain.vision.benchmark import BenchmarkAggregator, BenchmarkSample
from brain.vision.overlay import render_jpeg_overlay
from brain.vision.presentation import VisionArtifactPublisher
from brain.vision.runtime import DetectorPort, RuntimeState, VisionRuntime
from brain.vision.tracking import IoUAssociationTracker
from brain.vision.ultralytics import UltralyticsYoloDetector


class _PayloadResolver(Protocol):
    def resolve(self, payload_hash: str) -> bytes: ...


class CurrentGStreamerPayloadResolver:
    """Resolve only the currently authenticated source after a reconnect."""

    def __init__(self, source: "HashVerifiedGStreamerSource") -> None:
        self._source = source

    def replace_source(self, source: "HashVerifiedGStreamerSource") -> None:
        self._source = source

    def resolve(self, payload_hash: str) -> bytes:
        return self._source.resolve(payload_hash)


class HashVerifiedGStreamerSource:
    """Record only bytes that the GStreamer frame contract has hash-bound."""

    def __init__(self, adapter: GStreamerIngestAdapter, pipeline: AppSinkPipeline) -> None:
        self._adapter = adapter
        # The host records the payload while its appsink buffer is mapped.  The
        # domain adapter receives the same immutable bytes and creates the
        # authoritative SHA-256; we compare before exposing anything to YOLO.
        self._payload_supplier = lambda: getattr(pipeline, "last_payload", None)
        self._payloads: dict[str, bytes] = {}

    @property
    def adapter(self) -> GStreamerIngestAdapter:
        return self._adapter

    def poll(self):  # type: ignore[no-untyped-def]
        frame = self._adapter.poll()
        if frame is None:
            return None
        payload = self._payload_supplier()
        if payload is None:
            # The standard adapter deliberately hides payload bytes. Hosts use
            # ``register_payload`` immediately after their appsink pull.
            raise RuntimeError("GStreamer host did not register payload bytes for the accepted frame.")
        self.register_payload(frame.payload_hash, payload)
        return frame

    def register_payload(self, payload_hash: str, payload: bytes) -> None:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("GStreamer payload must be non-empty bytes.")
        if hashlib.sha256(payload).hexdigest() != payload_hash:
            raise ValueError("GStreamer payload does not match its accepted frame hash.")
        existing = self._payloads.setdefault(payload_hash, payload)
        if existing != payload:
            raise ValueError("GStreamer payload hash collision with conflicting bytes.")

    def resolve(self, payload_hash: str) -> bytes:
        try:
            return self._payloads[payload_hash]
        except KeyError as error:
            raise ValueError("No hash-verified GStreamer payload is available for inference.") from error

    def reconnect(self, stream_session_id: str) -> None:
        self._adapter.reconnect(stream_session_id)


class _GstBuffer:
    def __init__(self, sample, captured_at: datetime) -> None:  # type: ignore[no-untyped-def]
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        self.captured_at = captured_at
        self.mime_type = structure.get_name()
        self.width_px = int(structure.get_value("width"))
        self.height_px = int(structure.get_value("height"))
        gst_buffer = sample.get_buffer()
        success, mapping = gst_buffer.map(1)  # Gst.MapFlags.READ; no GI symbol outside host creation.
        if not success:
            raise RuntimeError("GStreamer could not map the appsink buffer for reading.")
        try:
            self._payload = bytes(mapping.data)
        finally:
            gst_buffer.unmap(mapping)

    def payload_bytes(self) -> bytes:
        return self._payload


class GstAppSinkHost:
    """Lazy ``gi``/Gst host adapter; unavailable runtimes fail closed at startup."""

    def __init__(self, pipeline_spec: str, binding: StreamBinding) -> None:
        if not pipeline_spec.strip():
            raise ValueError("A non-empty GStreamer --pipeline spec is required.")
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError) as error:  # pragma: no cover - host guard
            raise RuntimeError("GStreamer Python bindings are unavailable; install PyGObject/Gst on the camera host.") from error
        Gst.init(None)
        pipeline = Gst.parse_launch(pipeline_spec)
        sink = pipeline.get_by_name("vision_sink")
        if sink is None:
            raise ValueError("GStreamer pipeline must include appsink name=vision_sink.")
        self.binding = binding
        self._gst = Gst
        self._pipeline = pipeline
        self._sink = sink
        self.last_payload: bytes | None = None
        pipeline.set_state(Gst.State.PLAYING)

    def pull_buffer(self) -> _GstBuffer | None:
        message = self._pipeline.get_bus().pop_filtered(self._gst.MessageType.ERROR | self._gst.MessageType.EOS)
        if message is not None:
            if message.type == self._gst.MessageType.EOS:
                raise RuntimeError("GStreamer source reached end-of-stream.")
            error, debug = message.parse_error()
            raise RuntimeError(f"GStreamer source error: {error}; {debug or 'no debug detail'}")
        sample = self._sink.try_pull_sample(0)
        if sample is None:
            return None
        buffer = _GstBuffer(sample, datetime.now(UTC))
        self.last_payload = buffer.payload_bytes()
        return buffer

    def close(self) -> None:
        self._pipeline.set_state(self._gst.State.NULL)


def run_live_pipeline(
    source: HashVerifiedGStreamerSource,
    detector: DetectorPort,
    *,
    status_path: Path,
    frame_path: Path,
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    max_iterations: int,
    idle_sleep_seconds: float = 0.05,
    reconnect_factory: Callable[[int, str], HashVerifiedGStreamerSource] | None = None,
    max_reconnects: int = 3,
) -> dict[str, object]:
    """Poll a bounded number of frames and publish only genuine observations."""
    if type(max_iterations) is not int or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer.")
    if not isinstance(idle_sleep_seconds, (int, float)) or idle_sleep_seconds < 0:
        raise ValueError("idle_sleep_seconds must be non-negative.")
    if type(max_reconnects) is not int or max_reconnects < 0:
        raise ValueError("max_reconnects must be a non-negative integer.")
    runtime = VisionRuntime(detector, tracker=IoUAssociationTracker())
    publisher = VisionArtifactPublisher(status_path, frame_path)
    processed = unavailable = idle = reconnects = 0
    last_payload: bytes | None = None
    samples: list[BenchmarkSample] = []
    previous_source_drops = 0

    for _ in range(max_iterations):
        observed_at = now()
        accepted = runtime.ingest_once(source)
        outcome = runtime.process_next(observed_at)
        if outcome.state is RuntimeState.PROCESSED:
            processed += 1
            last_payload = source.resolve(outcome.frame.payload_hash)  # type: ignore[union-attr]
            source_drops = outcome.frame.dropped_frames  # type: ignore[union-attr]
            samples.append(BenchmarkSample(
                latency_ms=outcome.frame.latency_ms,  # type: ignore[union-attr]
                dropped_frames=max(0, source_drops - previous_source_drops),
            ))
            previous_source_drops = source_drops
            publisher.publish(outcome.detection, outcome.health, now=observed_at, render=lambda result: render_jpeg_overlay(last_payload, result))
        elif outcome.state is RuntimeState.UNAVAILABLE:
            unavailable += 1
            if last_payload is not None:
                publisher.publish(None, outcome.health, now=observed_at, render=lambda _result: last_payload)
            last_payload = None
            if reconnect_factory is None or reconnects >= max_reconnects:
                break
            try:
                replacement = reconnect_factory(reconnects + 1, source.adapter.binding.stream_session_id)
                if not isinstance(replacement, HashVerifiedGStreamerSource):
                    raise TypeError("reconnect factory must return a HashVerifiedGStreamerSource")
                if replacement.adapter.binding.stream_session_id == source.adapter.binding.stream_session_id:
                    raise ValueError("reconnect factory must rotate the stream session ID")
            except Exception:
                # The unavailable artifact was already published.  Do not turn
                # a failed recovery attempt into an empty detection result.
                break
            source = replacement
            runtime.reconnect(observed_at)
            previous_source_drops = 0
            reconnects += 1
        elif outcome.state is RuntimeState.IDLE:
            idle += 1
            if not accepted:
                sleep(float(idle_sleep_seconds))

    benchmark = BenchmarkAggregator("gstreamer-live").aggregate(samples) if samples else None
    return {
        "contract_version": "vision_live_pipeline.v1",
        "model_id": detector.model_id,
        "model_version": detector.model_version,
        "processed_frames": processed,
        "unavailable_frames": unavailable,
        "idle_polls": idle,
        "reconnects": reconnects,
        "stream_session_id": source.adapter.binding.stream_session_id,
        "benchmark": None if benchmark is None else {
            "sample_count": benchmark.sample_count,
            "p50_latency_ms": benchmark.p50_latency_ms,
            "p95_latency_ms": benchmark.p95_latency_ms,
            "dropped_frames": benchmark.dropped_frames,
        },
    }


def _approved_weights_path(weights_path: Path) -> Path:
    if not weights_path.is_file():
        raise ValueError("YOLO requires an existing local --weights file; downloads are disabled.")
    return weights_path.resolve()


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only local YOLO Vision pipeline from GStreamer appsink.")
    parser.add_argument("--pipeline", required=True, help="Gst launch spec containing appsink name=vision_sink")
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
    binding = StreamBinding(args.device_id, args.camera_id, args.stream_session_id)
    host = GstAppSinkHost(args.pipeline, binding)
    active_host = [host]
    try:
        adapter = GStreamerIngestAdapter(host, binding=binding, calibration_version=args.calibration_version, clock=lambda: datetime.now(UTC))
        source = HashVerifiedGStreamerSource(adapter, host)
        payload_resolver = CurrentGStreamerPayloadResolver(source)
        detector = UltralyticsYoloDetector("research-yolo11n", weights.name, payload_resolver, weights_path=str(weights))

        def reconnect_factory(attempt: int, _previous_session: str) -> HashVerifiedGStreamerSource:
            rotated_binding = StreamBinding(
                args.device_id, args.camera_id, f"{args.stream_session_id}-reconnect-{attempt}",
            )
            replacement_host = GstAppSinkHost(args.pipeline, rotated_binding)
            replacement_adapter = GStreamerIngestAdapter(
                replacement_host, binding=rotated_binding, calibration_version=args.calibration_version,
                clock=lambda: datetime.now(UTC),
            )
            replacement = HashVerifiedGStreamerSource(replacement_adapter, replacement_host)
            previous_host = active_host[0]
            active_host[0] = replacement_host
            payload_resolver.replace_source(replacement)
            previous_host.close()
            return replacement

        report = run_live_pipeline(
            source, detector, status_path=args.status_path, frame_path=args.frame_path,
            now=lambda: datetime.now(UTC), sleep=time.sleep, max_iterations=args.max_iterations,
            idle_sleep_seconds=args.idle_sleep, reconnect_factory=reconnect_factory,
            max_reconnects=args.max_reconnects,
        )
    finally:
        active_host[0].close()
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.report_path is None:
        print(payload)
    else:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    main()
