"""Deterministic P0 detection, tracking, and latency benchmark aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
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
