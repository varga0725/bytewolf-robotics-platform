"""The Budapest scene must stay reproducible from committed sources.

The world is generated and git-ignored, so the snapshot and the generator are
the only things standing between the project and a scene nobody can rebuild.
Overpass serves today's OSM, so a lost snapshot cannot be re-downloaded -- it
would come back as a different city.
"""

import gzip
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from simulation.worlds.build_budapest_world import DEFAULT_OSM_SNAPSHOT, DEFAULT_OUTPUT_WORLD


class BudapestWorldSourceTests(unittest.TestCase):
    def test_the_openstreetmap_snapshot_is_committed(self) -> None:
        self.assertTrue(
            DEFAULT_OSM_SNAPSHOT.is_file(),
            f"{DEFAULT_OSM_SNAPSHOT} is the only reproducible source of the scene",
        )

    def test_the_snapshot_is_stored_compressed(self) -> None:
        """The raw XML is 7.5 MB; gzip keeps it under 1 MB in the repository."""
        self.assertEqual(DEFAULT_OSM_SNAPSHOT.suffix, ".gz")
        self.assertLess(DEFAULT_OSM_SNAPSHOT.stat().st_size, 2_000_000)

    def test_the_snapshot_still_holds_the_openstreetmap_data_it_claims(self) -> None:
        with gzip.open(DEFAULT_OSM_SNAPSHOT, "rb") as stream:
            root = ET.fromstring(stream.read())

        self.assertEqual(root.tag, "osm")
        self.assertGreater(len(root.findall("way")), 1000)

    def test_the_generated_world_is_not_treated_as_a_source(self) -> None:
        """A committed product would drift from the snapshot it claims to come from."""
        ignore_rules = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("simulation/worlds/generated/", ignore_rules)
        self.assertIn("generated", DEFAULT_OUTPUT_WORLD.parts)


if __name__ == "__main__":
    unittest.main()
