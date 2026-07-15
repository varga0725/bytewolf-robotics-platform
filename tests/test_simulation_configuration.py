"""Regression checks for the reproducible PX4/Gazebo launch surface."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SimulationConfigurationTests(unittest.TestCase):
    def test_twin_configuration_declares_the_base_profile_and_safety_limits(self) -> None:
        configuration = (ROOT / "platforms/x500v2/config/twin.yaml").read_text()

        self.assertIn("id: x500v2_reference_01", configuration)
        self.assertIn("base: gz_x500", configuration)
        self.assertIn("hardware_baseline: holybro_x500_v2_developer_kit_v0", configuration)
        self.assertIn("active_payload_profile: factory_base", configuration)
        self.assertIn("max_altitude_m: 20", configuration)
        self.assertIn("max_radius_m: 50", configuration)

    def test_launch_script_exposes_all_documented_x500_profiles(self) -> None:
        launcher = (ROOT / "simulation/launch/run_px4_gazebo.zsh").read_text()

        self.assertIn("PX4_ROOT=${PX4_ROOT:A}", launcher)
        for target in (
            "gz_x500",
            "gz_x500_vision",
            "gz_x500_depth",
            "gz_x500_mono_cam",
            "gz_x500_mono_cam_down",
            "gz_x500_lidar_down",
            "gz_x500_lidar_front",
            "gz_x500_lidar_2d",
        ):
            self.assertIn(target, launcher)

    def test_validation_script_checks_required_native_dependencies(self) -> None:
        validator = (ROOT / "simulation/launch/validate_px4_gazebo.zsh").read_text()

        for dependency in ("cmake", "ninja", "gz", "brew"):
            self.assertIn(dependency, validator)


if __name__ == "__main__":
    unittest.main()
