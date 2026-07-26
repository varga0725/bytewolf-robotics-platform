from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.benchmark import (
    BenchmarkAggregator,
    BenchmarkSample,
    build_benchmark_manifest,
    write_benchmark_manifest,
)


class VisionBenchmarkTests(unittest.TestCase):
    def test_latency_percentiles_and_tracking_kpis_are_deterministic(self) -> None:
        report = BenchmarkAggregator("recorded-fixture").aggregate((
            BenchmarkSample(10, true_positives=2, false_positives=1, false_negatives=0, id_switches=1, fragmentations=0),
            BenchmarkSample(20, true_positives=1, false_positives=0, false_negatives=1, id_switches=0, fragmentations=1),
            BenchmarkSample(30, true_positives=1, false_positives=0, false_negatives=0, id_switches=0, fragmentations=0),
        ))

        self.assertEqual(report.p50_latency_ms, 20)
        self.assertEqual(report.p95_latency_ms, 30)
        self.assertEqual(report.precision, .8)
        self.assertEqual(report.recall, .8)
        self.assertEqual(report.id_switches, 1)
        self.assertEqual(report.fragmentations, 1)

    def test_manifest_hash_binds_fixture_model_config_and_kpis(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture.jsonl"
            weights = root / "weights.pt"
            config = root / "models.v1.yaml"
            fixture.write_bytes(b"fixture")
            weights.write_bytes(b"weights")
            config.write_bytes(b"config")
            report = BenchmarkAggregator("fixture.jsonl").aggregate((BenchmarkSample(10),))

            manifest = build_benchmark_manifest(
                source_path=fixture, model_id="research-yolo11n", model_version="weights.pt",
                report=report, generated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
                model_weights_path=weights, model_config_path=config,
            )
            target = root / "benchmark.manifest.json"
            write_benchmark_manifest(target, manifest)
            document = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(document["contract_version"], "vision_benchmark_manifest.v1")
            self.assertEqual(document["source_sha256"], hashlib.sha256(b"fixture").hexdigest())
            self.assertEqual(document["model_weights_sha256"], hashlib.sha256(b"weights").hexdigest())
            self.assertEqual(document["model_config_sha256"], hashlib.sha256(b"config").hexdigest())
            self.assertEqual(document["benchmark"]["p95_latency_ms"], 10.0)
            self.assertNotIn("payload", document)


if __name__ == "__main__":
    unittest.main()
