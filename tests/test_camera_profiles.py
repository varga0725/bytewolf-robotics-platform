"""The camera overlay raises resolution without editing PX4's tree.

The generator only rewrites width and height, so the tests check the resolution
changes, everything else (the FOV, the mesh includes) is left alone, and the PX4
source is untouched.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.gazebo.camera_profiles import (
    CameraProfileError,
    create_camera_overlay,
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
