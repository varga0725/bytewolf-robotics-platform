"""Deterministic P0 detection, tracking, and latency benchmark aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from math import ceil
import os
from pathlib import Path
from statistics import median
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkSample:
    latency_ms: float
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    id_switches: int = 0
    fragmentations: int = 0
    dropped_frames: int = 0

    def __post_init__(self) -> None:
        if self.latency_ms < 0 or any(value < 0 for value in self.__dict__.values() if isinstance(value, int)):
            raise ValueError("benchmark values must be non-negative")


@dataclass(frozen=True)
class BenchmarkReport:
    source_id: str
    sample_count: int
    p50_latency_ms: float
    p95_latency_ms: float
    precision: float
    recall: float
    id_switches: int
    fragmentations: int
    dropped_frames: int


BENCHMARK_MANIFEST_V1 = "vision_benchmark_manifest.v1"


@dataclass(frozen=True)
class BenchmarkManifest:
    """Immutable reproducibility record for an offline Vision benchmark."""

    contract_version: str
    source_id: str
    source_sha256: str
    model_id: str
    model_version: str
    model_weights_sha256: str | None
    model_config_sha256: str
    generated_at: datetime
    report: BenchmarkReport

    def __post_init__(self) -> None:
        if self.contract_version != BENCHMARK_MANIFEST_V1:
            raise ValueError("Unsupported benchmark manifest version.")
        if not all(isinstance(value, str) and value for value in (self.source_id, self.model_id, self.model_version)):
            raise ValueError("Benchmark manifest requires source and model identity.")
        for value in (self.source_sha256, self.model_config_sha256, self.model_weights_sha256):
            if value is not None and (not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value)):
                raise ValueError("Benchmark manifest hashes must be lowercase SHA-256 values.")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("Benchmark manifest timestamp must be timezone-aware.")
        if not isinstance(self.report, BenchmarkReport):
            raise ValueError("Benchmark manifest requires a BenchmarkReport.")

    def document(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_weights_sha256": self.model_weights_sha256,
            "model_config_sha256": self.model_config_sha256,
            "generated_at": self.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "benchmark": {
                "sample_count": self.report.sample_count,
                "p50_latency_ms": self.report.p50_latency_ms,
                "p95_latency_ms": self.report.p95_latency_ms,
                "precision": self.report.precision,
                "recall": self.report.recall,
                "id_switches": self.report.id_switches,
                "fragmentations": self.report.fragmentations,
                "dropped_frames": self.report.dropped_frames,
            },
        }


class BenchmarkAggregator:
    def __init__(self, source_id: str) -> None:
        if not source_id:
            raise ValueError("source_id is required")
        self._source_id = source_id

    def aggregate(self, samples: Iterable[BenchmarkSample]) -> BenchmarkReport:
        collected = tuple(samples)
        if not collected:
            raise ValueError("at least one benchmark sample is required")
        latencies = sorted(sample.latency_ms for sample in collected)
        tp = sum(sample.true_positives for sample in collected)
        fp = sum(sample.false_positives for sample in collected)
        fn = sum(sample.false_negatives for sample in collected)
        return BenchmarkReport(
            source_id=self._source_id,
            sample_count=len(collected),
            p50_latency_ms=float(median(latencies)),
            p95_latency_ms=latencies[ceil(len(latencies) * .95) - 1],
            precision=tp / (tp + fp) if tp + fp else 0.0,
            recall=tp / (tp + fn) if tp + fn else 0.0,
            id_switches=sum(sample.id_switches for sample in collected),
            fragmentations=sum(sample.fragmentations for sample in collected),
            dropped_frames=sum(sample.dropped_frames for sample in collected),
        )


def build_benchmark_manifest(
    *,
    source_path: Path,
    model_id: str,
    model_version: str,
    report: BenchmarkReport,
    generated_at: datetime,
    model_config_path: Path,
    model_weights_path: Path | None = None,
) -> BenchmarkManifest:
    """Hash-bind a recorded fixture, model configuration and KPI report."""
    if not isinstance(report, BenchmarkReport):
        raise ValueError("Benchmark manifest requires a BenchmarkReport.")
    return BenchmarkManifest(
        BENCHMARK_MANIFEST_V1, report.source_id, _sha256_file(source_path), model_id,
        model_version, None if model_weights_path is None else _sha256_file(model_weights_path),
        _sha256_file(model_config_path), generated_at, report,
    )


def write_benchmark_manifest(path: Path, manifest: BenchmarkManifest) -> None:
    """Atomically write a metadata-only benchmark manifest, never frames/weights."""
    if not isinstance(path, Path) or not path.name:
        raise ValueError("Benchmark manifest output path is required.")
    if not isinstance(manifest, BenchmarkManifest):
        raise ValueError("Benchmark manifest output requires a BenchmarkManifest.")
    payload = json.dumps(manifest.document(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    if not isinstance(path, Path) or not path.is_file():
        raise ValueError("Benchmark manifest requires an existing regular source/config/model file.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
