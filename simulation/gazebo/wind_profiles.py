"""Create inspectable, fixed-speed Gazebo wind worlds for X500 SITL evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re


SUPPORTED_WIND_SPEEDS_M_S = (3.0, 6.0, 10.0)
_WIND_ELEMENT = re.compile(r"<linear_velocity>\s*([^<]+?)\s*</linear_velocity>")


@dataclass(frozen=True)
class WindWorldFixture:
    """A generated world and the exact horizontal wind vector it declares."""

    source_world: Path
    output_world: Path
    north_m_s: float
    east_m_s: float

    @property
    def speed_m_s(self) -> float:
        return (self.north_m_s**2 + self.east_m_s**2) ** 0.5


class WindProfileError(ValueError):
    """Raised when a wind fixture cannot prove its configured condition."""


def render_fixed_speed_wind_world(source: str, speed_m_s: float) -> str:
    """Return a world whose Gazebo wind vector is exactly ``speed_m_s`` north.

    The fixture edits one existing, wind-enabled PX4 world rather than assuming
    Gazebo support.  It fails closed if that world does not expose exactly one
    wind velocity element.
    """
    if not isfinite(speed_m_s) or speed_m_s not in SUPPORTED_WIND_SPEEDS_M_S:
        allowed = ", ".join(f"{speed:g}" for speed in SUPPORTED_WIND_SPEEDS_M_S)
        raise WindProfileError(f"Wind speed must be one of {allowed} m/s.")
    matches = tuple(_WIND_ELEMENT.finditer(source))
    if len(matches) != 1:
        raise WindProfileError("Source Gazebo world must contain exactly one wind linear_velocity element.")
    wind_value = f"{speed_m_s:g} 0 0"
    return source[: matches[0].start(1)] + wind_value + source[matches[0].end(1) :]


def create_wind_fixture(source_world: Path, output_world: Path, speed_m_s: float) -> WindWorldFixture:
    """Write a reproducible, explicitly configured 3/6/10 m/s world fixture."""
    try:
        source = source_world.read_text(encoding="utf-8")
    except OSError as error:
        raise WindProfileError(f"Cannot read source Gazebo world '{source_world}': {error.strerror}.") from error
    rendered = render_fixed_speed_wind_world(source, speed_m_s)
    output_world.parent.mkdir(parents=True, exist_ok=True)
    output_world.write_text(rendered, encoding="utf-8")
    return WindWorldFixture(source_world, output_world, speed_m_s, 0.0)


def parse_arguments(arguments: tuple[str, ...] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an auditable fixed-speed Gazebo wind world.")
    parser.add_argument("--speed", type=float, required=True, help="Wind speed: 3, 6, or 10 m/s.")
    parser.add_argument("--source-world", type=Path, required=True)
    parser.add_argument("--output-world", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: tuple[str, ...] | None = None) -> None:
    parsed = parse_arguments(arguments)
    fixture = create_wind_fixture(parsed.source_world, parsed.output_world, parsed.speed)
    print(
        f"Wind fixture: {fixture.output_world} "
        f"({fixture.speed_m_s:g} m/s north; source {fixture.source_world})"
    )


if __name__ == "__main__":
    main()
