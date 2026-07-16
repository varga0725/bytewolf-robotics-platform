"""Reproducible fixed-speed Gazebo wind fixture coverage."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.gazebo.wind_profiles import (
    WindProfileError,
    create_wind_fixture,
    render_fixed_speed_wind_world,
)


_SOURCE = """<sdf><world><wind><linear_velocity>5 2 0</linear_velocity></wind></world></sdf>"""


class WindProfileTests(unittest.TestCase):
    def test_renders_each_required_speed_as_an_exact_horizontal_vector(self) -> None:
        for speed in (3.0, 6.0, 10.0):
            with self.subTest(speed=speed):
                rendered = render_fixed_speed_wind_world(_SOURCE, speed)
                self.assertIn(f"<linear_velocity>{speed:g} 0 0</linear_velocity>", rendered)

    def test_rejects_unrequested_speeds_and_missing_wind_configuration(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "3, 6, 10"):
            render_fixed_speed_wind_world(_SOURCE, 5.0)
        with self.assertRaisesRegex(WindProfileError, "exactly one"):
            render_fixed_speed_wind_world("<sdf/>", 3.0)

    def test_writes_an_inspectable_fixture(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "windy.sdf"
            output = Path(directory) / "wind-6.sdf"
            source.write_text(_SOURCE, encoding="utf-8")

            fixture = create_wind_fixture(source, output, 6.0)

            self.assertEqual(fixture.speed_m_s, 6.0)
            self.assertEqual(output.read_text(encoding="utf-8"), render_fixed_speed_wind_world(_SOURCE, 6.0))


if __name__ == "__main__":
    unittest.main()
