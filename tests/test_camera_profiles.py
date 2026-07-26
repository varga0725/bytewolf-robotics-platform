"""The camera overlay raises resolution without editing PX4's tree.

The generator only rewrites width and height, so the tests check the resolution
changes, everything else (the FOV, the mesh includes) is left alone, and the PX4
source is untouched.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.gazebo.camera_profiles import (
    create_camera_overlay,
    declared_camera_fov,
    declared_camera_resolution,
    CameraProfileError,
    render_camera_horizontal_fov,
    render_named_camera_model,
    render_high_res_mono_cam,
)


_SOURCE = (
    "<sdf><model name='mono_cam'><link name=\"camera_link\"><sensor name='imager' type='camera'>"
    "<camera><horizontal_fov>1.74</horizontal_fov><image><width>1280</width><height>960</height></image>"
    "</camera><update_rate>30</update_rate></sensor></link></model></sdf>"
)


class RenderTests(unittest.TestCase):
    def test_raises_only_the_resolution(self) -> None:
        rendered = render_high_res_mono_cam(_SOURCE, 1920, 1080)

        self.assertIn("<width>1920</width>", rendered)
        self.assertIn("<height>1080</height>", rendered)
        self.assertNotIn("<width>1280</width>", rendered)
        # The FOV and structure are untouched.
        self.assertIn("<horizontal_fov>1.74</horizontal_fov>", rendered)
        self.assertIn("<update_rate>30</update_rate>", rendered)

    def test_rejects_a_source_without_exactly_one_resolution(self) -> None:
        with self.assertRaisesRegex(CameraProfileError, "exactly one"):
            render_high_res_mono_cam("<sdf/>", 1920, 1080)

    def test_rejects_a_non_positive_resolution(self) -> None:
        with self.assertRaisesRegex(CameraProfileError, "positive"):
            render_high_res_mono_cam(_SOURCE, 0, 1080)

    def test_names_a_camera_component_for_a_merged_airframe(self) -> None:
        rendered = render_named_camera_model(_SOURCE, model_name="bytewolf_front_cam", link_name="front_camera_link")

        self.assertIn("<model name='bytewolf_front_cam'>", rendered)
        self.assertIn('<link name="front_camera_link">', rendered)

    def test_raises_only_the_fov(self) -> None:
        rendered = render_camera_horizontal_fov(_SOURCE, 2.793)

        self.assertIn("<horizontal_fov>2.793</horizontal_fov>", rendered)
        self.assertNotIn("<horizontal_fov>1.74</horizontal_fov>", rendered)
        # Resolution and structure are untouched.
        self.assertIn("<width>1280</width>", rendered)
        self.assertIn("<update_rate>30</update_rate>", rendered)

    def test_rejects_a_source_without_exactly_one_fov(self) -> None:
        with self.assertRaisesRegex(CameraProfileError, "exactly one horizontal_fov"):
            render_camera_horizontal_fov("<sdf/>", 2.793)

    def test_rejects_a_non_physical_fov(self) -> None:
        with self.assertRaisesRegex(CameraProfileError, r"\(0, pi\)"):
            render_camera_horizontal_fov(_SOURCE, 4.0)


class DeclaredFovTests(unittest.TestCase):
    def test_the_front_camera_declares_the_hawkeye_fov(self) -> None:
        # The twin front_rgb is the Hawkeye 4K Split V5 at 160 degrees.
        self.assertAlmostEqual(declared_camera_fov(), 2.793, places=3)

    def test_a_camera_without_a_usable_fov_fails_closed(self) -> None:
        with self.assertRaisesRegex(CameraProfileError, "usable horizontal FOV"):
            declared_camera_fov(camera="no_such_camera")


class CreateOverlayTests(unittest.TestCase):
    def test_writes_an_overlay_and_leaves_the_source_untouched(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_models = root / "px4-models"
            (source_models / "mono_cam").mkdir(parents=True)
            source_sdf = source_models / "mono_cam" / "model.sdf"
            source_sdf.write_text(_SOURCE, encoding="utf-8")
            models_root = root / "overlay"

            overlay = create_camera_overlay(source_models, models_root, width=1920, height=1080)

            self.assertEqual((overlay.width, overlay.height), (1920, 1080))
            written = (models_root / "mono_cam" / "model.sdf").read_text(encoding="utf-8")
            self.assertIn("<width>1920</width>", written)
            self.assertTrue((models_root / "mono_cam" / "model.config").is_file())
            # PX4's source model is not modified.
            self.assertEqual(source_sdf.read_text(encoding="utf-8"), _SOURCE)

    def test_full_sensor_overlay_combines_down_camera_and_lidar(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_models = root / "px4-models"
            (source_models / "mono_cam").mkdir(parents=True)
            (source_models / "mono_cam" / "model.sdf").write_text(_SOURCE, encoding="utf-8")
            overlay = root / "overlay"

            create_camera_overlay(source_models, overlay, include_lidar_2d=True)

            model = (overlay / "x500_mono_cam_down" / "model.sdf").read_text(encoding="utf-8")
            self.assertIn('<link name="front_camera_link">', model)
            self.assertIn('<link name="down_camera_link">', model)
            self.assertIn('<pose relative_to="base_link">.18 0 .04 0 0 0</pose>', model)
            self.assertIn('<pose relative_to="base_link">0 0 -.08 0 1.5707 0</pose>', model)
            self.assertIn('<uri>model://lidar_2d_v2</uri><pose>.12 0 .26 0 0 0</pose>', model)
            self.assertIn('<pose relative_to="base_link">.12 0 .26 0 0 0</pose>', model)
            self.assertIn("sensor name='front_imager'", model)
            self.assertIn("sensor name='down_imager'", model)
            self.assertIn("model://lidar_2d_v2", model)


if __name__ == "__main__":
    unittest.main()


class OverlayDriftTests(unittest.TestCase):
    """The overlay in the repo must be exactly what the renderer produces.

    It is generated output that the launcher rewrites on every run, and it drifted:
    the tracked copy held 1280x720 while the renderer defaulted to 960x540 and the
    twin declared 1920x1080 — three resolutions for one camera. Two model
    directories were also still tracked that nothing generates any more.

    Skipped without PX4's tree, which CI does not have; there it proves nothing
    and cannot run.
    """

    SOURCE_MODELS = Path("PX4-Autopilot/Tools/simulation/gz/models")
    OVERLAY = Path("simulation/artifacts/full-sensors-overlay")

    def setUp(self) -> None:
        if not (self.SOURCE_MODELS / "mono_cam" / "model.sdf").is_file():
            self.skipTest("PX4's Gazebo models are not checked out here.")

    def _render(self) -> dict[str, bytes]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_camera_overlay(self.SOURCE_MODELS, root, include_lidar_2d=True)
            return {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()
            }

    def test_rendering_twice_produces_the_same_bytes(self) -> None:
        self.assertEqual(self._render(), self._render())

    def test_the_tracked_overlay_matches_a_fresh_render(self) -> None:
        rendered = self._render()
        tracked = {
            path.relative_to(self.OVERLAY).as_posix(): path.read_bytes()
            for path in sorted(self.OVERLAY.rglob("*")) if path.is_file()
        }

        self.assertEqual(
            sorted(tracked), sorted(rendered),
            "the overlay holds files the renderer does not write, or is missing some",
        )
        for name in sorted(rendered):
            with self.subTest(name=name):
                self.assertEqual(tracked[name], rendered[name])

    def test_the_overlay_renders_at_the_resolution_the_twin_declares(self) -> None:
        width, height = declared_camera_resolution()
        model = (self.OVERLAY / "mono_cam" / "model.sdf").read_text(encoding="utf-8")

        self.assertIn(f"<width>{width}</width>", model)
        self.assertIn(f"<height>{height}</height>", model)
