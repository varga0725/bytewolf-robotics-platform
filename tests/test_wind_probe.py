"""Coverage for confirming a wind fixture from the vehicle's own attitude.

The probe exists to catch a wind run that never felt wind, so these tests pin
the two outcomes that matter: a tilted vehicle confirms, a level one does not.
"""

import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from simulation.gazebo.wind_probe import (
    GazeboPoseObserver,
    WindProbeError,
    expected_hover_tilt_deg,
    observe_tilt,
    tilt_deg_from_orientation,
)


def _quaternion_for_pitch(tilt_deg: float) -> dict[str, float]:
    half = math.radians(tilt_deg) / 2.0
    return {"x": 0.0, "y": math.sin(half), "z": 0.0, "w": math.cos(half)}


def _pose_message(tilt_deg: float, altitude_m: float, model: str = "x500_0") -> str:
    return json.dumps(
        {
            "pose": [
                {"name": "ground_plane", "position": {}, "orientation": {"w": 1}},
                {
                    "name": model,
                    "position": {"z": altitude_m},
                    "orientation": _quaternion_for_pitch(tilt_deg),
                },
            ]
        }
    )


class TiltFromOrientationTests(unittest.TestCase):
    def test_reads_the_angle_between_the_vehicle_up_axis_and_the_worlds(self) -> None:
        for tilt in (0.0, 2.5, 8.0, 30.0):
            with self.subTest(tilt=tilt):
                self.assertAlmostEqual(tilt_deg_from_orientation(_quaternion_for_pitch(tilt)), tilt, places=4)

    def test_treats_omitted_components_as_zero(self) -> None:
        """Gazebo leaves zero-valued components out of its pose messages."""
        self.assertAlmostEqual(tilt_deg_from_orientation({"w": 1}), 0.0)

    def test_rejects_a_non_numeric_orientation(self) -> None:
        with self.assertRaisesRegex(WindProbeError, "orientation.x"):
            tilt_deg_from_orientation({"x": "sideways", "w": 1})


class ExpectedHoverTiltTests(unittest.TestCase):
    def test_derives_the_tilt_from_the_fixtures_own_wind_force(self) -> None:
        """2.0 kg pushed at 0.1425 1/s by 10 m/s, held up by 2.0643 kg of weight."""
        self.assertAlmostEqual(expected_hover_tilt_deg(10.0, 0.1425, 2.0, 2.0643), 8.0, places=1)

    def test_scales_with_wind_speed(self) -> None:
        gentle = expected_hover_tilt_deg(3.0, 0.1425, 2.0, 2.0643)
        strong = expected_hover_tilt_deg(10.0, 0.1425, 2.0, 2.0643)

        self.assertAlmostEqual(gentle, 2.4, places=1)
        self.assertLess(gentle, strong)

    def test_rejects_inputs_that_cannot_produce_a_tilt(self) -> None:
        with self.assertRaisesRegex(WindProbeError, "wind speed"):
            expected_hover_tilt_deg(0.0, 0.1425, 2.0, 2.0643)
        with self.assertRaisesRegex(WindProbeError, "total mass"):
            expected_hover_tilt_deg(10.0, 0.1425, 2.0, float("nan"))


class ObserveTiltTests(unittest.TestCase):
    def test_confirms_a_vehicle_flying_the_modelled_wind(self) -> None:
        messages = [_pose_message(8.0, altitude_m=2.0) for _ in range(20)]

        observation = observe_tilt(messages, "x500_0", 8.0)

        self.assertTrue(observation.matches_expected_wind)
        self.assertAlmostEqual(observation.median_airborne_tilt_deg, 8.0, places=3)
        self.assertEqual(observation.airborne_samples, 20)

    def test_refuses_a_level_vehicle_that_never_felt_the_wind(self) -> None:
        """A fixture that failed to load leaves the vehicle level in hover."""
        messages = [_pose_message(0.0, altitude_m=2.0) for _ in range(20)]

        observation = observe_tilt(messages, "x500_0", 8.0)

        self.assertFalse(observation.matches_expected_wind)
        self.assertIn("did not fly the modelled wind", observation.detail)

    def test_ignores_the_ground_where_tilt_says_nothing_about_wind(self) -> None:
        grounded = [_pose_message(0.0, altitude_m=0.0) for _ in range(50)]
        airborne = [_pose_message(8.0, altitude_m=2.0) for _ in range(10)]

        observation = observe_tilt(grounded + airborne, "x500_0", 8.0)

        self.assertEqual(observation.samples, 60)
        self.assertEqual(observation.airborne_samples, 10)
        self.assertTrue(observation.matches_expected_wind)

    def test_refuses_a_verdict_from_too_few_airborne_samples(self) -> None:
        observation = observe_tilt([_pose_message(8.0, altitude_m=2.0)], "x500_0", 8.0)

        self.assertFalse(observation.matches_expected_wind)
        self.assertIsNone(observation.median_airborne_tilt_deg)
        self.assertIn("at least 5", observation.detail)

    def test_ignores_other_models_and_a_truncated_stream(self) -> None:
        messages = [_pose_message(8.0, 2.0, model="other_drone") for _ in range(10)]
        messages += [_pose_message(8.0, 2.0) for _ in range(10)]
        messages.append('{"pose": [{"name": "x500_0", "positi')

        observation = observe_tilt(messages, "x500_0", 8.0)

        self.assertEqual(observation.airborne_samples, 10)
        self.assertTrue(observation.matches_expected_wind)

    def test_tolerance_admits_controller_transients_but_not_still_air(self) -> None:
        for tilt, expected_match in ((8.0, True), (9.5, True), (6.5, True), (3.0, False), (0.0, False)):
            with self.subTest(tilt=tilt):
                messages = [_pose_message(tilt, altitude_m=2.0) for _ in range(20)]

                self.assertEqual(observe_tilt(messages, "x500_0", 8.0).matches_expected_wind, expected_match)


class GazeboPoseObserverTests(unittest.TestCase):
    def test_subscribes_on_the_interface_the_launcher_pins_the_server_to(self) -> None:
        """A mismatched interface discovers nothing, which reads as a wind failure."""
        with TemporaryDirectory() as directory, patch("subprocess.Popen") as popen:
            capture = Path(directory) / "poses.jsonl"

            with GazeboPoseObserver("windy", "x500_0", capture):
                pass

            command = popen.call_args.args[0]
            self.assertEqual(popen.call_args.kwargs["env"]["GZ_IP"], "127.0.0.1")
            self.assertIn("/world/windy/pose/info", command)
            self.assertIn("--json-output", command)


if __name__ == "__main__":
    unittest.main()
