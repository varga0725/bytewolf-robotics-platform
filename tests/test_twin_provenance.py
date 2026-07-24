"""Every unmeasured twin parameter must say how it gets resolved.

A null in twin.yaml means nobody has measured the real X500 V2, but the
simulation still flies on some number. Without a recorded source or plan, that
gap is invisible, and an unmeasured parameter reads exactly like a settled one.
"""

from collections.abc import Iterator, Mapping
from pathlib import Path
import unittest

import yaml


TWIN_PATH = Path(__file__).resolve().parents[1] / "shared/config/x500v2/twin.yaml"

# The sections describing physical reality. Everything else is either a policy
# the project chooses (safety limits) or a simulator setting, not a measurement.
CRITICAL_SECTIONS = ("vehicle", "propulsion", "aerodynamics", "battery", "sensors")

VALID_STATUSES = frozenset({"simulated", "derived", "vendor", "measured", "unknown"})


def _load_twin() -> Mapping:
    return yaml.safe_load(TWIN_PATH.read_text(encoding="utf-8"))


def _null_paths(node: object, prefix: str) -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _null_paths(value, f"{prefix}.{key}" if prefix else str(key))
    elif node is None:
        yield prefix


class TwinProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.twin = _load_twin()
        self.provenance = self.twin.get("provenance")

    def test_the_twin_records_provenance_at_all(self) -> None:
        self.assertIsInstance(self.provenance, Mapping)
        self.assertTrue(self.provenance)

    def test_every_unmeasured_critical_parameter_has_a_source_or_a_plan(self) -> None:
        """A null without a plan is an unmeasured value passing as a settled one."""
        for section in CRITICAL_SECTIONS:
            for path in _null_paths(self.twin.get(section), section):
                with self.subTest(parameter=path):
                    self.assertIn(
                        path,
                        self.provenance,
                        f"{path} is null but has no provenance entry; record its source or measurement plan",
                    )

    def test_every_provenance_entry_states_a_known_status_and_a_plan(self) -> None:
        for path, entry in self.provenance.items():
            with self.subTest(parameter=path):
                self.assertIsInstance(entry, Mapping, f"{path} provenance must be a mapping")
                self.assertIn(entry.get("status"), VALID_STATUSES, f"{path} has an unknown status")
                plan = entry.get("plan")
                self.assertIsInstance(plan, str, f"{path} must state how the real value gets resolved")
                self.assertTrue(plan.strip(), f"{path} has an empty plan")

    def test_a_simulated_parameter_names_the_value_the_simulation_actually_flies(self) -> None:
        """'simulated' is only honest if it says which number is in force, and from where."""
        for path, entry in self.provenance.items():
            if entry.get("status") != "simulated":
                continue
            with self.subTest(parameter=path):
                self.assertIn("simulated_value", entry, f"{path} must record the value in force")
                self.assertIsInstance(entry.get("simulated_source"), str, f"{path} must name its source")

    def test_provenance_does_not_describe_parameters_that_no_longer_exist(self) -> None:
        """A stale entry would claim a plan for a parameter nobody reads."""
        known = {
            path
            for section in CRITICAL_SECTIONS
            for path in _all_paths(self.twin.get(section), section)
        }

        for path in self.provenance:
            with self.subTest(parameter=path):
                self.assertIn(path, known, f"{path} has provenance but is not a twin parameter")

    def test_the_drag_coefficient_is_not_claimed_as_measured(self) -> None:
        """It is extrapolated from a paper about a different, lighter airframe."""
        entry = self.provenance["aerodynamics.linear_drag_coefficient_kg_s"]

        self.assertEqual(entry["status"], "derived")
        self.assertIn("wind generator", entry["plan"].lower())


def _all_paths(node: object, prefix: str) -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield child
            yield from _all_paths(value, child)
    else:
        yield prefix


if __name__ == "__main__":
    unittest.main()
