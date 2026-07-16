"""Regression checks for the reproducible PX4/Gazebo launch surface."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SimulationConfigurationTests(unittest.TestCase):
    def test_twin_configuration_declares_the_base_profile_and_safety_limits(self) -> None:
        configuration = (ROOT / "shared/config/x500v2/twin.yaml").read_text()

        self.assertIn("id: x500v2_reference_01", configuration)
        self.assertIn("base: gz_x500", configuration)
        self.assertIn("hardware_baseline: holybro_x500_v2_developer_kit_v0", configuration)
        self.assertIn("active_payload_profile: factory_base", configuration)
        self.assertIn("max_altitude_m: 20", configuration)
        self.assertIn("max_radius_m: 50", configuration)

    def test_launch_script_exposes_all_documented_x500_profiles(self) -> None:
        launcher = (ROOT / "simulation/gazebo/launch/run_px4_gazebo.zsh").read_text()

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
        validator = (ROOT / "simulation/gazebo/launch/validate_px4_gazebo.zsh").read_text()

        for dependency in ("cmake", "ninja", "gz", "brew"):
            self.assertIn(dependency, validator)

    def test_headless_launcher_starts_a_gazebo_server_before_px4(self) -> None:
        launcher = (ROOT / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh").read_text()

        self.assertIn("gz sim -r -s", launcher)
        self.assertIn("PX4_GZ_STANDALONE=1", launcher)
        self.assertIn("GZ_SIM_RESOURCE_PATH", launcher)
        self.assertIn("GZ_IP=127.0.0.1", launcher)
        self.assertIn("trap cleanup EXIT INT TERM", launcher)

    def test_headless_launcher_runs_px4_without_an_interactive_shell(self) -> None:
        """A piped headless process must not emit an unbounded PX4 prompt stream."""
        launcher = (ROOT / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh").read_text()

        self.assertIn('PX4_BINARY="$PX4_BUILD_DIR/bin/px4"', launcher)
        self.assertIn('"$PX4_BINARY" -d', launcher)

    def test_headless_launcher_owns_and_reaps_both_simulator_children(self) -> None:
        launcher = (ROOT / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh").read_text()

        self.assertIn('PX4_PID=$!', launcher)
        self.assertIn('stop_child "$PX4_PID"', launcher)
        self.assertIn('stop_child "$GZ_SERVER_PID"', launcher)
        self.assertIn('kill -KILL "$child_pid"', launcher)


if __name__ == "__main__":
    unittest.main()
