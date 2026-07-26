"""The overhead basemap must be measurable, or it is decoration on a map.

Every number here is one the browser uses to turn a pixel into a metre. A wrong
scale or a wrong rotation does not look broken — it looks like a map, and moves
every target the operator picks.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from simulation.gazebo.map_view import (
    DEFAULT_HORIZONTAL_FOV_RAD,
    MapViewError,
    camera_sdf,
    capture_frame,
    metres_per_pixel,
    spawn_map_camera,
    write_map_view,
)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class ScaleTests(unittest.TestCase):
    def test_the_scale_matches_the_measured_shift(self) -> None:
        """Moving the camera 40 m shifted the render 176 px; the geometry must agree.

        That measurement is the only reason this module can claim a metre on the
        grid is a metre on the picture.
        """
        resolution = metres_per_pixel(
            height_above_ground_m=158.0, horizontal_fov_rad=DEFAULT_HORIZONTAL_FOV_RAD, pixels=800
        )

        self.assertAlmostEqual(40.0 / resolution, 176.0, delta=4.0)

    def test_a_camera_on_the_ground_has_no_scale_rather_than_a_default(self) -> None:
        with self.assertRaises(MapViewError):
            metres_per_pixel(height_above_ground_m=0.0, horizontal_fov_rad=1.0, pixels=800)

    def test_an_impossible_field_of_view_is_refused(self) -> None:
        with self.assertRaises(MapViewError):
            metres_per_pixel(height_above_ground_m=100.0, horizontal_fov_rad=math.pi, pixels=800)


class SpawnTests(unittest.TestCase):
    def test_the_camera_carries_no_body_that_could_touch_the_vehicle(self) -> None:
        sdf = camera_sdf(model_name="m", topic="t", pixels=64, horizontal_fov_rad=1.0)

        self.assertIn("<static>true</static>", sdf)
        self.assertNotIn("<collision", sdf)
        self.assertNotIn("<inertial", sdf)

    def test_an_earlier_camera_is_removed_before_a_new_one_is_placed(self) -> None:
        calls: list[tuple[str, ...]] = []
        spawn_map_camera(
            world="baylands", east_m=205, north_m=155, height_m=160,
            run=lambda arguments: calls.append(arguments) or _completed("data: true"),
        )

        self.assertIn("/world/baylands/remove", calls[0])
        self.assertIn("/world/baylands/create", calls[1])

    def test_a_refused_spawn_is_an_error_rather_than_a_silent_blank_map(self) -> None:
        with self.assertRaisesRegex(MapViewError, "refused"):
            spawn_map_camera(
                world="baylands", east_m=0, north_m=0, height_m=160,
                run=lambda _arguments: _completed("data: false"),
            )


class CaptureTests(unittest.TestCase):
    def _frame_line(self, **overrides: object) -> str:
        import base64

        message = {
            "width": 2, "height": 2, "pixelFormatType": "RGB_INT8",
            "data": base64.b64encode(bytes(12)).decode(),
        }
        message.update(overrides)
        return json.dumps(message)

    def test_a_frame_is_returned_with_its_own_dimensions(self) -> None:
        pixels, width, height = capture_frame(run=lambda _a: _completed(self._frame_line()))

        self.assertEqual((width, height), (2, 2))
        self.assertEqual(len(pixels), 12)

    def test_a_truncated_frame_is_refused_rather_than_padded(self) -> None:
        import base64

        line = self._frame_line(data=base64.b64encode(bytes(6)).decode())

        with self.assertRaisesRegex(MapViewError, "truncated"):
            capture_frame(run=lambda _a: _completed(line))

    def test_an_unknown_pixel_format_is_refused_rather_than_guessed(self) -> None:
        line = self._frame_line(pixelFormatType="BAYER_RGGB8")

        with self.assertRaisesRegex(MapViewError, "cannot decode"):
            capture_frame(run=lambda _a: _completed(line))

    def test_silence_on_the_topic_is_an_error_not_an_empty_map(self) -> None:
        with self.assertRaisesRegex(MapViewError, "No frame"):
            capture_frame(run=lambda _a: _completed(""))


class WriteTests(unittest.TestCase):
    def test_the_written_metadata_says_which_way_is_up_and_how_far_a_pixel_is(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_map_view(
                output_dir=output, pixels=bytes(3 * 4 * 6), width=4, height=6,
                ground_resolution_m=0.228, world="baylands", height_above_ground_m=158.0,
            )
            document = json.loads((output / "map-view.json").read_text())

        self.assertEqual(document["orientation"], "north_up_east_right")
        self.assertAlmostEqual(document["metres_per_pixel"], 0.228)
        self.assertEqual(document["world"], "baylands")

    def test_the_quarter_turn_that_puts_north_up_is_actually_applied(self) -> None:
        """Raw-up is East and raw-left is North, so the saved image is transposed.

        A 4x6 frame must come back 6x4: if the rotation is ever dropped, the
        picture still looks like a park and every bearing on it is wrong.
        """
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_map_view(
                output_dir=output, pixels=bytes(3 * 4 * 6), width=4, height=6,
                ground_resolution_m=0.228, world="baylands", height_above_ground_m=158.0,
            )
            document = json.loads((output / "map-view.json").read_text())

        self.assertEqual((document["width"], document["height"]), (6, 4))

    def test_the_centre_is_the_origin_the_dashboard_grid_uses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_map_view(
                output_dir=output, pixels=bytes(3 * 4 * 4), width=4, height=4,
                ground_resolution_m=0.228, world="baylands", height_above_ground_m=158.0,
            )
            document = json.loads((output / "map-view.json").read_text())

        self.assertEqual((document["centre_north_m"], document["centre_east_m"]), (0.0, 0.0))

    def test_no_temporary_file_is_left_for_the_dashboard_to_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_map_view(
                output_dir=output, pixels=bytes(3 * 4 * 4), width=4, height=4,
                ground_resolution_m=0.228, world="baylands", height_above_ground_m=158.0,
            )
            names = sorted(path.name for path in output.iterdir())

        self.assertEqual(names, ["map-view.jpg", "map-view.json"])


if __name__ == "__main__":
    unittest.main()
