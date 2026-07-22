from __future__ import annotations

import unittest

from brain.vision.benchmark import BenchmarkAggregator, BenchmarkSample


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


if __name__ == "__main__":
    unittest.main()
