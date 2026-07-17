"""Reproducible fixed-speed Gazebo wind fixture coverage.

The fixture's whole purpose is to prove a wind condition, so every test here
guards a way the fixture could silently claim wind it does not apply.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.gazebo.wind_profiles import (
    WindProfileError,
    create_wind_fixture,
    render_fixed_speed_wind_world,
    render_wind_enabled_airframe_model,
    render_wind_enabled_base_model,
    render_wind_server_config,
)


_SOURCE_WORLD = "<sdf><world name='windy'><wind><linear_velocity>5 2 0</linear_velocity></wind></world></sdf>"
_SOURCE_SERVER_CONFIG = (
    "<server_config>\n  <plugins>\n"
    '    <plugin entity_name="*" entity_type="world" filename="gz-sim-physics-system"'
    ' name="gz::sim::systems::Physics"/>\n'
    "  </plugins>\n</server_config>\n"
)
_SOURCE_BASE_MODEL = (
    "<sdf><model name='x500_base'><link name=\"base_link\">"
    "<visual name='v'><uri>model://x500_base/meshes/body.dae</uri></visual>"
    "</link><link name='rotor_0'></link></model></sdf>"
)
_SOURCE_AIRFRAME_MODEL = (
    "<sdf><model name='x500'><include merge='true'><uri>model://x500_base</uri></include></model></sdf>"
)


class WindWorldTests(unittest.TestCase):
    def test_renders_each_required_speed_as_an_exact_horizontal_vector(self) -> None:
        for speed in (3.0, 6.0, 10.0):
            with self.subTest(speed=speed):
                rendered = render_fixed_speed_wind_world(_SOURCE_WORLD, speed)
                self.assertIn(f"<linear_velocity>{speed:g} 0 0</linear_velocity>", rendered)

    def test_rejects_unrequested_speeds_and_missing_wind_configuration(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "3, 6, 10"):
            render_fixed_speed_wind_world(_SOURCE_WORLD, 5.0)
        with self.assertRaisesRegex(WindProfileError, "exactly one"):
            render_fixed_speed_wind_world("<sdf/>", 3.0)

    def test_rejects_a_world_whose_plugins_would_disable_the_wind_system(self) -> None:
        """Gazebo loads a world's plugins or the server config's, never both."""
        with_plugin = _SOURCE_WORLD.replace("<wind>", '<plugin filename="x" name="y"/><wind>')

        with self.assertRaisesRegex(WindProfileError, "must declare no plugins"):
            render_fixed_speed_wind_world(with_plugin, 3.0)


class WindServerConfigTests(unittest.TestCase):
    def test_adds_the_wind_system_alongside_the_px4_systems(self) -> None:
        """A <wind> vector alone is inert: Gazebo needs the WindEffects system."""
        rendered = render_wind_server_config(_SOURCE_SERVER_CONFIG)

        self.assertIn('filename="gz-sim-wind-effects-system"', rendered)
        self.assertIn('name="gz::sim::systems::WindEffects"', rendered)
        self.assertIn("gz-sim-physics-system", rendered)
        self.assertLess(rendered.index("WindEffects"), rendered.index("</plugins>"))

    def test_rejects_a_source_that_already_drives_wind_itself(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "already loads a wind system"):
            render_wind_server_config(render_wind_server_config(_SOURCE_SERVER_CONFIG))

    def test_rejects_a_config_without_the_expected_plugin_list(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "exactly once"):
            render_wind_server_config("<server_config/>")


class WindEnabledModelTests(unittest.TestCase):
    def test_opts_the_airframe_body_into_wind(self) -> None:
        """Gazebo applies wind only to links that explicitly enable it."""
        rendered = render_wind_enabled_base_model(_SOURCE_BASE_MODEL)

        self.assertIn("<enable_wind>true</enable_wind>", rendered)
        self.assertLess(rendered.index("enable_wind"), rendered.index("</link>"))
        self.assertIn("<model name='x500_wind_base'>", rendered)

    def test_keeps_mesh_uris_pointing_at_the_read_only_px4_package(self) -> None:
        rendered = render_wind_enabled_base_model(_SOURCE_BASE_MODEL)

        self.assertIn("model://x500_base/meshes/body.dae", rendered)

    def test_rejects_a_base_model_without_the_expected_shape(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "must declare a base_link"):
            render_wind_enabled_base_model("<sdf><model name='x500_base'/></sdf>")
        with self.assertRaisesRegex(WindProfileError, "already configures wind"):
            render_wind_enabled_base_model(render_wind_enabled_base_model(_SOURCE_BASE_MODEL))

    def test_wires_the_airframe_to_the_wind_enabled_base(self) -> None:
        rendered = render_wind_enabled_airframe_model(_SOURCE_AIRFRAME_MODEL)

        self.assertIn("<uri>model://x500_wind_base</uri>", rendered)
        self.assertNotIn("<uri>model://x500_base</uri>", rendered)

    def test_rejects_an_airframe_that_does_not_include_the_stock_base_once(self) -> None:
        with self.assertRaisesRegex(WindProfileError, "exactly once"):
            render_wind_enabled_airframe_model("<sdf><model name='x500'/></sdf>")


class WindFixtureTests(unittest.TestCase):
    @staticmethod
    def _write_sources(root: Path) -> tuple[Path, Path, Path]:
        source_world = root / "windy.sdf"
        source_world.write_text(_SOURCE_WORLD, encoding="utf-8")
        source_config = root / "server.config"
        source_config.write_text(_SOURCE_SERVER_CONFIG, encoding="utf-8")
        source_models = root / "px4-models"
        (source_models / "x500_base").mkdir(parents=True)
        (source_models / "x500_base" / "model.sdf").write_text(_SOURCE_BASE_MODEL, encoding="utf-8")
        (source_models / "x500").mkdir(parents=True)
        (source_models / "x500" / "model.sdf").write_text(_SOURCE_AIRFRAME_MODEL, encoding="utf-8")
        return source_world, source_models, source_config

    def _create(self, root: Path, speed: float) -> tuple[Path, Path, Path]:
        source_world, source_models, source_config = self._write_sources(root)
        output_world = root / f"wind-{speed:g}.sdf"
        models_root = root / "overlay"
        output_config = root / "wind-server.config"
        create_wind_fixture(
            source_world,
            output_world,
            speed,
            source_models=source_models,
            models_root=models_root,
            source_server_config=source_config,
            output_server_config=output_config,
        )
        return output_world, models_root, output_config

    def test_writes_an_inspectable_world_wind_system_and_model_overlay(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_world, source_models, source_config = self._write_sources(root)
            models_root = root / "overlay"
            output_config = root / "wind-server.config"
            output_world = root / "wind-6.sdf"

            fixture = create_wind_fixture(
                source_world,
                output_world,
                6.0,
                source_models=source_models,
                models_root=models_root,
                source_server_config=source_config,
                output_server_config=output_config,
            )

            self.assertEqual(fixture.speed_m_s, 6.0)
            self.assertEqual((fixture.north_m_s, fixture.east_m_s), (0.0, 6.0))
            self.assertEqual(
                output_world.read_text(encoding="utf-8"), render_fixed_speed_wind_world(_SOURCE_WORLD, 6.0)
            )
            self.assertIn("WindEffects", output_config.read_text(encoding="utf-8"))
            self.assertIn(
                "<enable_wind>true</enable_wind>",
                (models_root / "x500_wind_base" / "model.sdf").read_text(encoding="utf-8"),
            )
            self.assertIn("model://x500_wind_base", (models_root / "x500" / "model.sdf").read_text(encoding="utf-8"))
            self.assertIn("<name>x500</name>", (models_root / "x500" / "model.config").read_text(encoding="utf-8"))

    def test_leaves_the_read_only_px4_sources_untouched(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._create(root, 3.0)

            self.assertEqual((root / "windy.sdf").read_text(encoding="utf-8"), _SOURCE_WORLD)
            self.assertEqual((root / "server.config").read_text(encoding="utf-8"), _SOURCE_SERVER_CONFIG)
            self.assertEqual(
                (root / "px4-models" / "x500_base" / "model.sdf").read_text(encoding="utf-8"), _SOURCE_BASE_MODEL
            )
            self.assertEqual(
                (root / "px4-models" / "x500" / "model.sdf").read_text(encoding="utf-8"), _SOURCE_AIRFRAME_MODEL
            )


if __name__ == "__main__":
    unittest.main()
