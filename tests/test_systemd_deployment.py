"""The installed topology must describe the integrated, read-only sensor stack."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNIT_ROOT = ROOT / "deploy" / "systemd"


class SystemdDeploymentTests(unittest.TestCase):
    def _unit(self, name: str) -> str:
        return (UNIT_ROOT / name).read_text(encoding="utf-8")

    def test_target_contains_every_integrated_service(self) -> None:
        target = self._unit("bytewolf.target")
        for service in (
            "bytewolf-sitl.service", "bytewolf-telemetry.service", "bytewolf-controlroom.service",
            "bytewolf-camera-front.service", "bytewolf-camera-down.service", "bytewolf-world-map.service",
        ):
            self.assertIn(service, target)

    def test_read_only_observers_cannot_take_down_the_core_stack(self) -> None:
        target = self._unit("bytewolf.target")
        self.assertIn(
            "Requires=bytewolf-sitl.service bytewolf-telemetry.service bytewolf-controlroom.service",
            target,
        )
        self.assertIn(
            "Wants=bytewolf-camera-front.service bytewolf-camera-down.service bytewolf-world-map.service",
            target,
        )

    def test_deployed_simulator_uses_the_only_combined_sensor_profile(self) -> None:
        self.assertIn("run_px4_gazebo_headless.zsh full-sensors", self._unit("bytewolf-sitl.service"))

    def test_camera_relays_use_the_python_310_gazebo_boundary_and_separate_sensors(self) -> None:
        front = self._unit("bytewolf-camera-front.service")
        down = self._unit("bytewolf-camera-down.service")
        for unit in (front, down):
            self.assertIn("/usr/bin/python3.10 -s", unit)
            self.assertIn("GZ_IP=127.0.0.1", unit)
            self.assertIn("--full-sensors", unit)
        self.assertIn("--sensor front", front)
        self.assertIn("--sensor down", down)

    def test_world_map_observer_is_the_read_only_recorder(self) -> None:
        unit = self._unit("bytewolf-world-map.service")
        self.assertIn("simulation.perception.survey_recorder", unit)
        self.assertNotIn("brain.cli.fly_", unit)

    def test_ros_bridge_is_not_an_always_on_service(self) -> None:
        services = "\n".join(path.read_text(encoding="utf-8") for path in UNIT_ROOT.glob("*.service"))
        self.assertNotIn("brain.cli.ros2_telemetry_bridge", services)

    def test_installer_never_publishes_the_unauthenticated_loopback_api(self) -> None:
        installer = self._unit("install-stack.sh")
        verifier = self._unit("verify-stack.sh")
        control_room = self._unit("bytewolf-controlroom.service")

        for document in (installer, verifier, control_room):
            self.assertNotIn("tailscale serve", document)
        self.assertNotIn("/usr/bin/tailscale", installer)
        self.assertIn("API_ROOT=http://127.0.0.1:8080", verifier)
        self.assertIn("127.0.0.1", control_room)

    def test_control_room_and_telemetry_are_pinned_to_simulation(self) -> None:
        for service in ("bytewolf-controlroom.service", "bytewolf-telemetry.service"):
            with self.subTest(service=service):
                self.assertIn(
                    "Environment=BYTEWOLF_DEPLOYMENT_MODE=simulation",
                    self._unit(service),
                )


if __name__ == "__main__":
    unittest.main()
