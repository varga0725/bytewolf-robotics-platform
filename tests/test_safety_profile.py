from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

from brain.safety.profile import SafetyProfileError, load_safety_profile


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "platforms/x500v2/config/twin.yaml"


class SafetyProfileTests(unittest.TestCase):
    def test_loads_the_versioned_x500_safety_contract_as_an_immutable_value(self) -> None:
        profile = load_safety_profile(PROFILE_PATH)

        self.assertEqual(profile.vehicle_id, "x500v2_reference_01")
        self.assertEqual(profile.max_altitude_m, 20.0)
        self.assertEqual(profile.max_radius_m, 50.0)
        self.assertEqual(profile.minimum_battery_percent_to_start, 40.0)
        with self.assertRaises(FrozenInstanceError):
            profile.max_altitude_m = 100.0  # type: ignore[misc]

    def test_rejects_missing_or_invalid_safety_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "twin.yaml"
            profile_path.write_text("vehicle:\n  id: x500\nsafety:\n  max_altitude_m: 0\n")

            with self.assertRaisesRegex(SafetyProfileError, "max_altitude_m"):
                load_safety_profile(profile_path)


if __name__ == "__main__":
    unittest.main()
