"""The read-only tool plugins the conversational turn needs: vision + world.

Both are read-only capabilities behind the Plugin SDK: they summarise evidence,
never control the drone, and fail soft when their artifact is missing.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from apps.plugins import vision_summary, world_query
from apps.plugins.vision_summary import VisionSummaryPlugin
from brain.plugin_sdk import PluginRegistry


_NOW = datetime(2026, 7, 24, 10, 0, 0, 200000, tzinfo=UTC)


def _det(label, confidence=0.9, x=1, y=1, w=2, h=2):
    return {"label": label, "confidence": confidence,
            "bounding_box": {"x_px": x, "y_px": y, "width_px": w, "height_px": h}}


def _summary_doc(validity="valid", observed_at="2026-07-24T10:00:00+00:00", max_age_s=0.5, detections=None):
    dets = detections if detections is not None else []
    return {
        "contract_version": "v0.1",
        "source": "camera:front_rgb",
        "observed_at": observed_at,
        "max_age_s": max_age_s,
        "validity": validity,
        "frame": {"width_px": 1280, "height_px": 720},
        "model_id": "yolo",
        "model_version": "v8",
        "detections": dets,
        "detection_count": len(dets),
    }


class VisionSummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vision-summary.json"

    def _plugin(self):
        return VisionSummaryPlugin(self.path, now=lambda: _NOW)

    def test_a_fresh_reading_is_summarised_as_fresh(self) -> None:
        self.path.write_text(json.dumps(_summary_doc(detections=[_det("marker")])), encoding="utf-8")
        plugin = self._plugin()
        plugin.start()
        summary = plugin.capabilities()["vision.summary"]()
        self.assertTrue(summary["available"])
        self.assertTrue(summary["fresh"])
        self.assertEqual(summary["detection_count"], 1)

    def test_a_stale_reading_is_not_fresh(self) -> None:
        self.path.write_text(
            json.dumps(_summary_doc(observed_at="2026-07-24T09:59:00+00:00")), encoding="utf-8"
        )
        summary = self._plugin().capabilities()["vision.summary"]()
        self.assertFalse(summary["fresh"])

    def test_a_missing_artifact_is_unavailable_not_an_error(self) -> None:
        summary = self._plugin().capabilities()["vision.summary"]()
        self.assertFalse(summary["available"])
        self.assertEqual(summary["detection_count"], 0)

    def test_a_contract_violating_artifact_is_unavailable(self) -> None:
        # A document that is not a valid VisionSummary is unavailable, not a crash.
        self.path.write_text(json.dumps({"contract_version": "v0.1", "detections": []}), encoding="utf-8")
        summary = self._plugin().capabilities()["vision.summary"]()
        self.assertFalse(summary["available"])

    def test_the_summary_carries_detection_identities(self) -> None:
        self.path.write_text(json.dumps(_summary_doc(detections=[_det("marker", 0.91)])), encoding="utf-8")
        summary = self._plugin().capabilities()["vision.summary"]()
        self.assertEqual(summary["detections"][0]["label"], "marker")
        self.assertEqual(summary["detections"][0]["confidence"], 0.91)

    def test_both_camera_feeds_are_aggregated(self) -> None:
        down = Path(self._tmp.name) / "down.json"
        self.path.write_text(json.dumps(_summary_doc(detections=[_det("front-marker")])), encoding="utf-8")
        down.write_text(json.dumps(_summary_doc(detections=[_det("down-pad")])), encoding="utf-8")
        plugin = VisionSummaryPlugin({"front": self.path, "down": down}, now=lambda: _NOW)
        plugin.start()
        summary = plugin.capabilities()["vision.summary"]()
        self.assertEqual(summary["detection_count"], 2)
        labels = {d["label"] for d in summary["detections"]}
        self.assertEqual(labels, {"front-marker", "down-pad"})

    def test_invocation_through_the_registry(self) -> None:
        self.path.write_text(json.dumps(_summary_doc()), encoding="utf-8")
        registry = PluginRegistry()
        vision_summary.register(registry, self.path)
        registry.start("vision.summary")
        result = registry.invoke("vision.summary")
        self.assertIn("detection_count", result)


class WorldQueryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "claims.jsonl"

    def test_a_missing_store_briefs_nothing_believed(self) -> None:
        # A missing log reads as an empty world (not an error), so the briefing
        # is the "nothing believed" line, and the plugin still returns a string.
        registry = PluginRegistry()
        world_query.register(registry, self.path)
        registry.start("world_memory.query")
        result = registry.invoke("world_memory.query")
        self.assertIn("Nincs érvényes", result["briefing"])

    def test_an_empty_store_briefs_nothing_believed(self) -> None:
        self.path.write_text("", encoding="utf-8")
        plugin = world_query.WorldMemoryQueryPlugin(self.path)
        plugin.start()
        briefing = plugin.capabilities()["world_memory.query"]()["briefing"]
        self.assertIn("Nincs érvényes", briefing)


class SafetyTests(unittest.TestCase):
    def test_read_only_plugins_never_import_flight_control(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in ("vision_summary.py", "world_query.py"):
            text = (root / "apps" / "plugins" / name).read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(plugin=name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


if __name__ == "__main__":
    unittest.main()
