"""Run a read-only recorded Vision Core fixture pipeline."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Sequence

from brain.vision.benchmark import (
    BenchmarkAggregator,
    BenchmarkSample,
    build_benchmark_manifest,
    write_benchmark_manifest,
)
from brain.vision.evaluation import EvaluationFrame, GroundTruthEvaluator, GroundTruthValidationError
from brain.vision.presentation import VisionArtifactPublisher
from brain.vision.overlay import render_jpeg_overlay
from brain.vision.recorded import AnnotatedFixtureDetector, RecordedFixtureError, RecordedJsonlIngest
from brain.vision.runtime import RuntimeState, TrackerPort, VisionRuntime
from brain.vision.tracking import IoUAssociationTracker
from brain.vision.ultralytics import UltralyticsYoloDetector


MODEL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "shared/config/vision/models.v1.yaml"


class _HashVerifiedRecordedPayloadResolver:
    """Expose only hash-bound fixture payloads to an inference adapter."""

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}

    def register(self, payload_hash: str, payload: bytes) -> None:
        if hashlib.sha256(payload).hexdigest() != payload_hash:
            raise RecordedFixtureError("recorded payload failed hash verification before inference")
        existing = self._payloads.setdefault(payload_hash, payload)
        if existing != payload:
            raise RecordedFixtureError("a payload hash resolved to conflicting recorded bytes")

    def resolve(self, payload_hash: str) -> bytes:
        try:
            return self._payloads[payload_hash]
        except KeyError as error:
            raise RecordedFixtureError("no hash-verified recorded payload is available for inference") from error


def _approved_weights_path(weights_path: Path | None) -> Path:
    if weights_path is None or not weights_path.is_file():
        raise ValueError("YOLO requires an approved local weights file supplied with --weights.")
    return weights_path.resolve()


def _detector_for(
    detector: str, source: RecordedJsonlIngest, weights_path: Path | None,
) -> tuple[object, _HashVerifiedRecordedPayloadResolver | None]:
    if detector == "annotations":
        return AnnotatedFixtureDetector(source), None
    if detector == "yolo":
        approved_weights = _approved_weights_path(weights_path)
        resolver = _HashVerifiedRecordedPayloadResolver()
        return (
            UltralyticsYoloDetector(
                "research-yolo11n", approved_weights.name, resolver, weights_path=str(approved_weights),
            ),
            resolver,
        )
    raise ValueError(f"Unsupported recorded detector: {detector}")


def run_recorded_pipeline(
    input_path: Path, status_path: Path, frame_path: Path, *, now: datetime,
    detector: str = "yolo", weights_path: Path | None = None,
    tracker: TrackerPort | None = None, metadata_path: Path | None = None,
    benchmark_manifest_path: Path | None = None,
) -> dict[str, object]:
    """Replay a fixture, publishing only verified observation artifacts."""
    source = RecordedJsonlIngest(input_path)
    selected_detector, payload_resolver = _detector_for(detector, source, weights_path)
    active_tracker = tracker if tracker is not None else IoUAssociationTracker()
    runtime = VisionRuntime(selected_detector, tracker=active_tracker)  # type: ignore[arg-type]
    publisher = VisionArtifactPublisher(status_path, frame_path, metadata_path)
    samples: list[BenchmarkSample] = []
    evaluation_frames: list[EvaluationFrame] = []
    processed = rejected = unavailable = 0
    last_payload: bytes | None = None
    last_result = None

    while not source.exhausted:
        frame = source.poll()
        accepted = frame is not None
        if frame is not None:
            payload = source.payload_for(frame)
            if payload_resolver is not None:
                payload_resolver.register(frame.payload_hash, payload)
            runtime.submit(frame)
        outcome = runtime.process_next(now)
        if outcome.state is RuntimeState.PROCESSED:
            processed += 1
            last_payload = source.payload_for(outcome.frame)  # type: ignore[arg-type]
            last_result = outcome.detection
            if source.has_ground_truth:
                evaluation_frames.append(EvaluationFrame(outcome.detection, source.ground_truth_for(outcome.frame)))  # type: ignore[arg-type]
            else:
                samples.append(BenchmarkSample(latency_ms=outcome.frame.latency_ms, dropped_frames=outcome.frame.dropped_frames))  # type: ignore[union-attr]
        elif outcome.state is RuntimeState.REJECTED:
            rejected += 1
        elif outcome.state is RuntimeState.UNAVAILABLE:
            unavailable += 1
        if not accepted and outcome.state is RuntimeState.IDLE:
            break
        if last_payload is not None:
            publisher.publish(outcome.detection, outcome.health, now=now, render=lambda result, payload=last_payload: render_jpeg_overlay(payload, result))

    health = runtime.health(now)
    if last_payload is not None:
        publisher.publish(last_result, health, now=now, render=lambda result, payload=last_payload: render_jpeg_overlay(payload, result))
    benchmark = None
    benchmark_document = None
    if evaluation_frames:
        evaluation = GroundTruthEvaluator().evaluate(evaluation_frames)
        benchmark = BenchmarkAggregator(str(input_path)).aggregate(evaluation.samples)
        benchmark_document = {
            "sample_count": benchmark.sample_count, "p50_latency_ms": benchmark.p50_latency_ms,
            "p95_latency_ms": benchmark.p95_latency_ms,
            "precision": benchmark.precision, "recall": benchmark.recall,
            "id_switches": benchmark.id_switches, "fragmentations": benchmark.fragmentations,
            "reacquisitions": evaluation.reacquisitions,
            "quality_kpis": "ground_truth_attached",
            "dropped_frames": benchmark.dropped_frames,
        }
    elif samples:
        benchmark = BenchmarkAggregator(str(input_path)).aggregate(samples)
        benchmark_document = {
            "sample_count": benchmark.sample_count, "p50_latency_ms": benchmark.p50_latency_ms,
            "p95_latency_ms": benchmark.p95_latency_ms,
            # Fixture annotations are predictions, not independently associated
            # ground truth.  Reporting zero-valued quality KPIs here would be
            # misleading; the benchmark harness fills these only with labels.
            "precision": None, "recall": None, "id_switches": None,
            "fragmentations": None, "reacquisitions": None,
            "quality_kpis": "unavailable_without_ground_truth",
            "dropped_frames": benchmark.dropped_frames,
        }
    if benchmark_manifest_path is not None and benchmark is not None:
        manifest = build_benchmark_manifest(
            source_path=input_path,
            model_id=selected_detector.model_id,
            model_version=selected_detector.model_version,
            report=benchmark,
            generated_at=now,
            model_config_path=MODEL_CONFIG_PATH,
            model_weights_path=weights_path if detector == "yolo" else None,
        )
        write_benchmark_manifest(benchmark_manifest_path, manifest)
    return {
        "contract_version": "vision_recorded_pipeline.v1",
        "source": str(input_path), "detector": detector,
        "model_id": selected_detector.model_id,
        "model_version": selected_detector.model_version,
        "processed_frames": processed, "rejected_frames": rejected,
        "unavailable_frames": unavailable,
        "benchmark": benchmark_document,
        "benchmark_manifest": None if benchmark_manifest_path is None else str(benchmark_manifest_path),
    }


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay hash-verified Vision JSONL fixtures without control access.")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--frame-path", type=Path, required=True)
    parser.add_argument("--metadata-path", type=Path, help="Optional local append-only Vision metadata JSONL")
    parser.add_argument("--benchmark-manifest-path", type=Path, help="Optional reproducible benchmark manifest JSON")
    parser.add_argument("--report-path", type=Path)
    parser.add_argument(
        "--detector", choices=("annotations", "yolo"), default="yolo",
        help="Research baseline is YOLO11n; annotations is only for deterministic fixtures/tests.",
    )
    parser.add_argument(
        "--weights", type=Path,
        help="Explicit existing local YOLO11n weights file; required with --detector yolo. Downloads are disabled.",
    )
    parser.add_argument("--now", default=None, help="RFC3339 timestamp used for deterministic replay")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_arguments(arguments)
    now = datetime.now(UTC) if args.now is None else datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    try:
        report = run_recorded_pipeline(
            args.input_path, args.status_path, args.frame_path, now=now,
            detector=args.detector, weights_path=args.weights, metadata_path=args.metadata_path,
            benchmark_manifest_path=args.benchmark_manifest_path,
        )
    except (GroundTruthValidationError, RecordedFixtureError, ValueError) as error:
        raise SystemExit(f"recorded vision fixture rejected: {error}") from error
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.report_path is None:
        print(payload)
    else:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    main()
