"""Vision is an observation domain and must have no flight-control dependency."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.boundaries import forbidden_vision_imports


class VisionImportBoundaryTests(unittest.TestCase):
    def test_vision_domain_has_no_flight_control_imports(self) -> None:
        root = Path(__file__).resolve().parents[1] / "brain" / "vision"
        self.assertEqual(forbidden_vision_imports(root), ())

    def test_rejects_mission_safety_and_dynamic_control_imports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text(
                "import brain.mission\nfrom brain.safety import gate\n"
                "import importlib as imports\nimports.import_module('brain.adapters.mavsdk_adapter')\n"
                "from importlib import import_module as load\nload('px4')\n__import__('mavsdk')\n",
                encoding="utf-8",
            )
            violations = forbidden_vision_imports(root)
        self.assertEqual(len(violations), 5)

    def test_rejects_an_importer_rebound_to_another_name(self) -> None:
        # A guard that only reads import statements is walked straight past by
        # `loader = import_module`, which reaches exactly the same modules.
        forms = (
            "from importlib import import_module\nloader = import_module\nloader('mavsdk')\n",
            "alias = __import__\nalias('mavsdk')\n",
            "import importlib\nil = importlib\nfn = il.import_module\nfn('mavsdk')\n",
        )
        for source in forms:
            with self.subTest(source=source.splitlines()[0]), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "bad.py").write_text(source, encoding="utf-8")
                violations = forbidden_vision_imports(root)
                self.assertEqual(len(violations), 1, violations)
                self.assertIn("mavsdk", violations[0])

    def test_an_ordinary_alias_is_not_a_violation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fine.py").write_text("import json\ndumps = json.dumps\ndumps({})\n", encoding="utf-8")
            self.assertEqual(forbidden_vision_imports(root), ())

    def test_rejects_a_nonliteral_dynamic_import(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text("module = 'safe'\n__import__(module)\n", encoding="utf-8")
            self.assertEqual(len(forbidden_vision_imports(root)), 1)
