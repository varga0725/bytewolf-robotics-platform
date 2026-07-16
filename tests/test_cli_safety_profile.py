from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
import unittest

from brain.cli import fly_return_to_home, fly_takeoff_hover_land, fly_waypoint_land


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "platforms/x500v2/config/twin.yaml"


class CliSafetyProfileTests(unittest.TestCase):
    def test_every_flight_cli_uses_the_versioned_safety_profile_by_default(self) -> None:
        for module in (fly_takeoff_hover_land, fly_waypoint_land, fly_return_to_home):
            with self.subTest(cli=module.__name__):
                arguments = module.parse_arguments(())
                self.assertEqual(arguments.safety_profile, PROFILE_PATH)

    def test_profile_path_can_be_selected_but_safety_limits_cannot_be_overridden(self) -> None:
        for module in (fly_takeoff_hover_land, fly_waypoint_land, fly_return_to_home):
            with self.subTest(cli=module.__name__):
                arguments = module.parse_arguments(("--safety-profile", str(PROFILE_PATH)))
                self.assertEqual(arguments.safety_profile, PROFILE_PATH)
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                    module.parse_arguments(("--max-altitude", "100"))


if __name__ == "__main__":
    unittest.main()
