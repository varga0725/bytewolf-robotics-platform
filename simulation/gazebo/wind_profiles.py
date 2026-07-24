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
* the server config gains the ``WindEffects`` system alongside PX4's systems,
  scaled to the twin's drag rather than left at Gazebo's default;
* an overlay X500 opts its ``base_link`` into wind and is spawned in place of
  the stock airframe via ``PX4_GZ_MODELS``.

Gazebo applies ``force = link_mass * scaling_factor * (wind_velocity -
link_velocity)``, i.e. drag linear in airspeed.  That happens to be the model
the literature validates for a quadrotor between ~2 and ~9 m/s, where rotor drag
dominates, so the scaling factor is just the twin's linear drag coefficient over
the airframe mass.  Gazebo's default of 1.0 is not a drag model at all: it drags
the vehicle up to wind speed like a balloon, which slides a grounded X500 across
the world at 10 m/s before it can arm.

Nothing under the PX4 checkout is modified; the overlay only ever adds files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re

import yaml


SUPPORTED_WIND_SPEEDS_M_S = (3.0, 6.0, 10.0)

DEFAULT_TWIN_PATH = Path(__file__).resolve().parents[2] / "shared/config/x500v2/twin.yaml"

# Gazebo's world frame is ENU, so the first component of the wind vector is the
# eastward speed.  The fixture keeps the wind purely horizontal and eastward.
_WIND_ELEMENT = re.compile(r"<linear_velocity>\s*([^<]+?)\s*</linear_velocity>")
_BASE_LINK_OPEN = re.compile(r"<link\s+name=['\"]base_link['\"]\s*>")
_MASS_ELEMENT = re.compile(r"<mass>\s*([^<]+?)\s*</mass>")
_LINK_CLOSE = "</link>"
_PLUGINS_CLOSE = "</plugins>"
_X500_BASE_URI = "model://x500_base"

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
    scaling_factor_per_s: float
    extrapolates_drag_model: bool
    airframe_mass_kg: float
    total_mass_kg: float

    @property
    def speed_m_s(self) -> float:
        return (self.north_m_s**2 + self.east_m_s**2) ** 0.5


class WindProfileError(ValueError):
    """Raised when a wind fixture cannot prove its configured condition."""


@dataclass(frozen=True)
class LinearDragModel:
    """The twin's literature-backed linear airframe drag: ||D|| = coefficient * airspeed."""

    coefficient_kg_s: float
    valid_airspeed_m_s: tuple[float, float]

    def scaling_factor_per_s(self, airframe_mass_kg: float) -> float:
        """Return Gazebo's factor, which divides the drag by the mass it pushes."""
        if not isfinite(airframe_mass_kg) or airframe_mass_kg <= 0.0:
            raise WindProfileError("Airframe mass must be a positive, finite value.")
        return self.coefficient_kg_s / airframe_mass_kg

    def extrapolates_at(self, airspeed_m_s: float) -> bool:
        """Report whether a wind speed sits outside the band the model is backed by."""
        lower, upper = self.valid_airspeed_m_s
        return not lower <= airspeed_m_s <= upper


def load_linear_drag_model(twin_path: Path) -> LinearDragModel:
    """Read the twin's drag model, failing closed rather than guessing a default."""
    try:
        document = yaml.safe_load(twin_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WindProfileError(f"Cannot read twin profile '{twin_path}': {error.strerror}.") from error
    except yaml.YAMLError as error:
        raise WindProfileError(f"Twin profile '{twin_path}' is not valid YAML.") from error

    aerodynamics = document.get("aerodynamics") if isinstance(document, dict) else None
    if not isinstance(aerodynamics, dict):
        raise WindProfileError("Twin profile must define an 'aerodynamics' mapping to model wind.")
    coefficient = aerodynamics.get("linear_drag_coefficient_kg_s")
    band = aerodynamics.get("linear_drag_valid_airspeed_m_s")
    if type(coefficient) not in (int, float) or not isfinite(float(coefficient)) or coefficient <= 0.0:
        raise WindProfileError(
            "Twin profile field 'aerodynamics.linear_drag_coefficient_kg_s' must be a positive number; "
            "wind cannot be simulated from an unknown drag."
        )
    if not isinstance(band, list) or len(band) != 2 or any(type(value) not in (int, float) for value in band):
        raise WindProfileError(
            "Twin profile field 'aerodynamics.linear_drag_valid_airspeed_m_s' must be a two-number band."
        )
    lower, upper = float(band[0]), float(band[1])
    if not (isfinite(lower) and isfinite(upper) and 0.0 <= lower < upper):
        raise WindProfileError("Twin profile drag validity band must be an increasing, finite, non-negative range.")
    return LinearDragModel(float(coefficient), (lower, upper))


def read_airframe_mass_kg(source: str) -> float:
    """Return the mass of the link the wind pushes, from PX4's own model."""
    match = _BASE_LINK_OPEN.search(source)
    if match is None:
        raise WindProfileError("Source X500 base model must declare a base_link to receive wind.")
    mass = _MASS_ELEMENT.search(source, match.end())
    if mass is None:
        raise WindProfileError("Source X500 base_link must declare a mass to scale the wind force.")
    try:
        value = float(mass.group(1))
    except ValueError as error:
        raise WindProfileError("Source X500 base_link mass must be a number.") from error
    if not isfinite(value) or value <= 0.0:
        raise WindProfileError("Source X500 base_link mass must be positive and finite.")
    return value


def read_total_mass_kg(source: str) -> float:
    """Return the whole airframe's mass, which the wind force is judged against."""
    masses = [float(match.group(1)) for match in _MASS_ELEMENT.finditer(source) if _is_number(match.group(1))]
    if not masses:
        raise WindProfileError("Source X500 base model must declare at least one mass.")
    total = sum(masses)
    if not isfinite(total) or total <= 0.0:
        raise WindProfileError("Source X500 total mass must be positive and finite.")
    return total


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def render_wind_effects_plugin(scaling_factor_per_s: float) -> str:
    """Return the wind system entry, scaled to the twin's drag.

    Gazebo's own default is 1.0, which is not a drag model: it accelerates the
    vehicle until it matches the wind, so it must always be set explicitly.
    """
    if not isfinite(scaling_factor_per_s) or scaling_factor_per_s <= 0.0:
        raise WindProfileError("Wind force scaling factor must be a positive, finite value.")
    return (
        '    <plugin entity_name="*" entity_type="world" filename="gz-sim-wind-effects-system"'
        ' name="gz::sim::systems::WindEffects">\n'
        f"      <force_approximation_scaling_factor>{scaling_factor_per_s:.6g}"
        "</force_approximation_scaling_factor>\n"
        "    </plugin>\n"
    )


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


def render_wind_server_config(source: str, scaling_factor_per_s: float) -> str:
    """Return PX4's Gazebo server config with the scaled wind system added.

    The wind system has to travel with PX4's own systems: Gazebo loads either a
    world's plugins or the server config's, never both.
    """
    if "WindEffects" in source:
        raise WindProfileError("Source server config already loads a wind system; the fixture cannot prove its speed.")
    if source.count(_PLUGINS_CLOSE) != 1:
        raise WindProfileError("Source server config must close its plugins element exactly once.")
    plugin = render_wind_effects_plugin(scaling_factor_per_s)
    return source.replace(_PLUGINS_CLOSE, f"{plugin}{_PLUGINS_CLOSE}")


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
    drag_model: LinearDragModel,
) -> WindWorldFixture:
    """Write a reproducible 3/6/10 m/s world, wind system, and the X500 that feels it."""
    base_source = _read(source_models / "x500_base" / "model.sdf", "source X500 base model")
    airframe_mass = read_airframe_mass_kg(base_source)
    total_mass = read_total_mass_kg(base_source)
    scaling_factor = drag_model.scaling_factor_per_s(airframe_mass)

    world = render_fixed_speed_wind_world(_read(source_world, "source Gazebo world"), speed_m_s)
    server_config = render_wind_server_config(
        _read(source_server_config, "source Gazebo server config"), scaling_factor
    )
    base_model = render_wind_enabled_base_model(base_source)
    airframe_model = render_wind_enabled_airframe_model(
        _read(source_models / AIRFRAME_MODEL_NAME / "model.sdf", "source X500 model")
    )

    _write(output_world, world)
    _write(output_server_config, server_config)
    _write(models_root / WIND_BASE_MODEL_NAME / "model.sdf", base_model)
    _write(models_root / WIND_BASE_MODEL_NAME / "model.config", _model_config(WIND_BASE_MODEL_NAME))
    _write(models_root / AIRFRAME_MODEL_NAME / "model.sdf", airframe_model)
    _write(models_root / AIRFRAME_MODEL_NAME / "model.config", _model_config(AIRFRAME_MODEL_NAME))
    return WindWorldFixture(
        source_world,
        output_world,
        output_server_config,
        models_root,
        0.0,
        speed_m_s,
        scaling_factor,
        drag_model.extrapolates_at(speed_m_s),
        airframe_mass,
        total_mass,
    )


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
    parser.add_argument(
        "--twin", type=Path, default=DEFAULT_TWIN_PATH, help="Twin profile holding the airframe drag model."
    )
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
        drag_model=load_linear_drag_model(parsed.twin),
    )
    extrapolation = " EXTRAPOLATED drag model" if fixture.extrapolates_drag_model else ""
    print(
        f"Wind fixture: {fixture.output_world} ({fixture.speed_m_s:g} m/s east; "
        f"scaling {fixture.scaling_factor_per_s:.6g} 1/s{extrapolation}; wind system {fixture.server_config}; "
        f"models {fixture.models_root}; source {fixture.source_world})"
    )


if __name__ == "__main__":
    main()
