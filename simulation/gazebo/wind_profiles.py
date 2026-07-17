"""Create inspectable, fixed-speed Gazebo wind fixtures for X500 SITL evidence.

A wind fixture is only honest if the wind actually reaches the airframe.  Gazebo
applies wind exclusively to links that opt in with ``<enable_wind>``, and only
when the ``WindEffects`` system is loaded.  PX4's stock worlds declare a
``<wind>`` vector but load no wind system, and its stock X500 opts no link into
wind, so a stock ``windy`` world exerts exactly zero force on the vehicle: a
free-falling stock X500 drifts 0 m in 10 m/s wind, the overlay one drifts ~58 m.

Every fixture therefore renders all three parts from the PX4 sources, and fails
closed when a source does not have the exact shape the rendering relies on:

* the world carries one exact horizontal vector, and no plugins of its own —
  any ``<plugin>`` in a world makes Gazebo ignore the server config entirely,
  which would drop both the wind system and PX4's own systems;
* the server config gains the ``WindEffects`` system alongside PX4's systems;
* an overlay X500 opts its ``base_link`` into wind and is spawned in place of
  the stock airframe via ``PX4_GZ_MODELS``.

Nothing under the PX4 checkout is modified; the overlay only ever adds files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re


SUPPORTED_WIND_SPEEDS_M_S = (3.0, 6.0, 10.0)

# Gazebo's world frame is ENU, so the first component of the wind vector is the
# eastward speed.  The fixture keeps the wind purely horizontal and eastward.
_WIND_ELEMENT = re.compile(r"<linear_velocity>\s*([^<]+?)\s*</linear_velocity>")
_BASE_LINK_OPEN = re.compile(r"<link\s+name=['\"]base_link['\"]\s*>")
_LINK_CLOSE = "</link>"
_PLUGINS_CLOSE = "</plugins>"
_X500_BASE_URI = "model://x500_base"

WIND_EFFECTS_PLUGIN = (
    '    <plugin entity_name="*" entity_type="world" filename="gz-sim-wind-effects-system"'
    ' name="gz::sim::systems::WindEffects"/>\n'
)

WIND_BASE_MODEL_NAME = "x500_wind_base"
AIRFRAME_MODEL_NAME = "x500"


@dataclass(frozen=True)
class WindWorldFixture:
    """A generated world, overlay model root, and the wind vector they declare."""

    source_world: Path
    output_world: Path
    server_config: Path
    models_root: Path
    north_m_s: float
    east_m_s: float

    @property
    def speed_m_s(self) -> float:
        return (self.north_m_s**2 + self.east_m_s**2) ** 0.5


class WindProfileError(ValueError):
    """Raised when a wind fixture cannot prove its configured condition."""


def render_fixed_speed_wind_world(source: str, speed_m_s: float) -> str:
    """Return a world whose Gazebo wind vector is exactly ``speed_m_s`` east.

    The fixture edits one existing, wind-declaring PX4 world rather than
    assuming Gazebo support.  It fails closed if that world does not expose
    exactly one wind velocity element, or declares a plugin of its own — a
    world plugin makes Gazebo ignore the server config that carries both the
    wind system and PX4's own systems, silently producing a wind-free run.
    """
    if not isfinite(speed_m_s) or speed_m_s not in SUPPORTED_WIND_SPEEDS_M_S:
        allowed = ", ".join(f"{speed:g}" for speed in SUPPORTED_WIND_SPEEDS_M_S)
        raise WindProfileError(f"Wind speed must be one of {allowed} m/s.")
    matches = tuple(_WIND_ELEMENT.finditer(source))
    if len(matches) != 1:
        raise WindProfileError("Source Gazebo world must contain exactly one wind linear_velocity element.")
    if "<plugin" in source:
        raise WindProfileError(
            "Source Gazebo world must declare no plugins; a world plugin disables the wind server config."
        )

    wind_value = f"{speed_m_s:g} 0 0"
    return source[: matches[0].start(1)] + wind_value + source[matches[0].end(1) :]


def render_wind_server_config(source: str) -> str:
    """Return PX4's Gazebo server config with the wind system added.

    The wind system has to travel with PX4's own systems: Gazebo loads either a
    world's plugins or the server config's, never both.
    """
    if "WindEffects" in source:
        raise WindProfileError("Source server config already loads a wind system; the fixture cannot prove its speed.")
    if source.count(_PLUGINS_CLOSE) != 1:
        raise WindProfileError("Source server config must close its plugins element exactly once.")
    return source.replace(_PLUGINS_CLOSE, f"{WIND_EFFECTS_PLUGIN}{_PLUGINS_CLOSE}")


def render_wind_enabled_base_model(source: str) -> str:
    """Return the X500 base with ``base_link`` opted into Gazebo wind.

    Mesh URIs keep pointing at the stock ``x500_base`` package, so the overlay
    adds one file and never copies PX4's assets.
    """
    if "enable_wind" in source:
        raise WindProfileError("Source X500 base model already configures wind; the fixture cannot prove its effect.")
    base_link = _BASE_LINK_OPEN.search(source)
    if base_link is None:
        raise WindProfileError("Source X500 base model must declare a base_link to receive wind.")
    link_close = source.find(_LINK_CLOSE, base_link.end())
    if link_close == -1:
        raise WindProfileError("Source X500 base model must close its base_link element.")

    renamed = _rename_model(source, "x500_base", WIND_BASE_MODEL_NAME)
    # Renaming rewrites only the model tag, which precedes base_link, and it can
    # only lengthen the text before it, so re-find the boundary on the result.
    renamed_link = _BASE_LINK_OPEN.search(renamed)
    assert renamed_link is not None, "Renaming the model must not remove base_link."
    renamed_close = renamed.find(_LINK_CLOSE, renamed_link.end())
    return renamed[:renamed_close] + "<enable_wind>true</enable_wind>" + renamed[renamed_close:]


def render_wind_enabled_airframe_model(source: str) -> str:
    """Return the X500 airframe wired to the wind-enabled base, not the stock one."""
    if source.count(_X500_BASE_URI) != 1:
        raise WindProfileError("Source X500 model must include the x500_base package exactly once.")
    return source.replace(_X500_BASE_URI, f"model://{WIND_BASE_MODEL_NAME}")


def _rename_model(source: str, current_name: str, new_name: str) -> str:
    pattern = re.compile(rf"(<model\s+name=['\"]){re.escape(current_name)}(['\"])")
    renamed, substitutions = pattern.subn(rf"\g<1>{new_name}\g<2>", source, count=1)
    if substitutions != 1:
        raise WindProfileError(f"Source model must declare exactly one '{current_name}' model.")
    return renamed


def _model_config(name: str) -> str:
    return (
        '<?xml version="1.0"?>\n<model>\n'
        f"  <name>{name}</name>\n"
        "  <version>1.0</version>\n"
        '  <sdf version="1.9">model.sdf</sdf>\n'
        "  <description>Generated ByteWolf wind fixture overlay; see simulation/gazebo/wind_profiles.py.</description>\n"
        "</model>\n"
    )


def _read(path: Path, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise WindProfileError(f"Cannot read {description} '{path}': {error.strerror}.") from error


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_wind_fixture(
    source_world: Path,
    output_world: Path,
    speed_m_s: float,
    *,
    source_models: Path,
    models_root: Path,
    source_server_config: Path,
    output_server_config: Path,
) -> WindWorldFixture:
    """Write a reproducible 3/6/10 m/s world, wind system, and the X500 that feels it."""
    world = render_fixed_speed_wind_world(_read(source_world, "source Gazebo world"), speed_m_s)
    server_config = render_wind_server_config(_read(source_server_config, "source Gazebo server config"))
    base_model = render_wind_enabled_base_model(
        _read(source_models / "x500_base" / "model.sdf", "source X500 base model")
    )
    airframe_model = render_wind_enabled_airframe_model(
        _read(source_models / AIRFRAME_MODEL_NAME / "model.sdf", "source X500 model")
    )

    _write(output_world, world)
    _write(output_server_config, server_config)
    _write(models_root / WIND_BASE_MODEL_NAME / "model.sdf", base_model)
    _write(models_root / WIND_BASE_MODEL_NAME / "model.config", _model_config(WIND_BASE_MODEL_NAME))
    _write(models_root / AIRFRAME_MODEL_NAME / "model.sdf", airframe_model)
    _write(models_root / AIRFRAME_MODEL_NAME / "model.config", _model_config(AIRFRAME_MODEL_NAME))
    return WindWorldFixture(source_world, output_world, output_server_config, models_root, 0.0, speed_m_s)


def parse_arguments(arguments: tuple[str, ...] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an auditable fixed-speed Gazebo wind fixture.")
    parser.add_argument("--speed", type=float, required=True, help="Wind speed: 3, 6, or 10 m/s.")
    parser.add_argument("--source-world", type=Path, required=True)
    parser.add_argument("--output-world", type=Path, required=True)
    parser.add_argument("--source-models", type=Path, required=True, help="PX4's read-only Gazebo model root.")
    parser.add_argument(
        "--models-root", type=Path, required=True, help="Where the wind-enabled X500 overlay is written."
    )
    parser.add_argument("--source-server-config", type=Path, required=True, help="PX4's read-only Gazebo server config.")
    parser.add_argument("--output-server-config", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: tuple[str, ...] | None = None) -> None:
    parsed = parse_arguments(arguments)
    fixture = create_wind_fixture(
        parsed.source_world,
        parsed.output_world,
        parsed.speed,
        source_models=parsed.source_models,
        models_root=parsed.models_root,
        source_server_config=parsed.source_server_config,
        output_server_config=parsed.output_server_config,
    )
    print(
        f"Wind fixture: {fixture.output_world} ({fixture.speed_m_s:g} m/s east; "
        f"wind system {fixture.server_config}; models {fixture.models_root}; source {fixture.source_world})"
    )


if __name__ == "__main__":
    main()
