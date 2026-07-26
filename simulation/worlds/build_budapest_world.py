#!/usr/bin/env python3
"""Generate a lightweight Gazebo world from OpenStreetMap data.

The first ByteWolf city scene covers Árpádföld and Mátyásföld in Budapest.
It intentionally uses low-poly building masses and major roads so it remains
fast enough for PX4 SITL while retaining real geographic coordinates.

The committed source is this script plus the OpenStreetMap snapshot under
``data/``; the world itself is generated and deliberately not committed. The
snapshot is what makes the scene reproducible: Overpass serves whatever OSM
holds today, so ``--refresh`` rebuilds a *different* city than the one earlier
flights flew in. Regenerating from the snapshot needs no network.
"""

from __future__ import annotations

import argparse
import gzip
import math
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


_WORLDS_ROOT = Path(__file__).resolve().parent
DEFAULT_OSM_SNAPSHOT = _WORLDS_ROOT / "data" / "budapest_arpadfold_matyasfold.osm.gz"
DEFAULT_OUTPUT_WORLD = _WORLDS_ROOT / "generated" / "budapest_arpadfold_matyasfold.sdf"


ORIGIN_LAT = 47.5135015  # Mátyásföld, Imre utca station
ORIGIN_LON = 19.1994922
# Mátyásföld + Árpádföld, with a small margin.
BBOX = (47.4905, 19.1780, 47.5420, 19.2325)  # south, west, north, east
EARTH_METERS_PER_DEGREE_LAT = 110_540.0
EARTH_METERS_PER_DEGREE_LON = 111_320.0 * math.cos(math.radians(ORIGIN_LAT))
USER_AGENT = "ByteWolf-Robotics-Platform/1.0 (local digital-twin generator)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def meters(lat: float, lon: float) -> tuple[float, float]:
    return (
        (lon - ORIGIN_LON) * EARTH_METERS_PER_DEGREE_LON,
        (lat - ORIGIN_LAT) * EARTH_METERS_PER_DEGREE_LAT,
    )


def _read_snapshot(path: Path) -> ET.Element:
    """Read the OSM snapshot, gzipped or not, so old plain caches still work."""
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as stream:
            return ET.fromstring(stream.read())
    return ET.parse(path).getroot()


def osm_query(cache_path: Path, refresh: bool) -> ET.Element:
    if cache_path.exists() and not refresh:
        return _read_snapshot(cache_path)

    # Only the download needs a certificate bundle, so the committed snapshot
    # regenerates the world without it and without a network.
    try:
        import certifi
    except ImportError as error:  # pragma: no cover - exercised only with --refresh
        raise SystemExit(
            "--refresh downloads from Overpass and needs certifi: .venv/bin/pip install -r requirements.txt"
        ) from error

    south, west, north, east = BBOX
    query = f"""[out:xml][timeout:180];
(
  way[\"building\"]({south},{west},{north},{east});
  way[\"highway\"~\"primary|secondary|tertiary\"]({south},{west},{north},{east});
);
(._;>;);
out body;"""
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240, context=ssl.create_default_context(cafile=certifi.where())) as response:
        payload = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.suffix == ".gz":
        with gzip.open(cache_path, "wb", compresslevel=9) as stream:
            stream.write(payload)
    else:
        cache_path.write_bytes(payload)
    return ET.fromstring(payload)


def tags(element: ET.Element) -> dict[str, str]:
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}


def building_height(osm_tags: dict[str, str]) -> float:
    for key in ("height", "building:height"):
        value = osm_tags.get(key, "").lower().replace("m", "").strip()
        try:
            return min(max(float(value), 3.5), 80.0)
        except ValueError:
            pass
    try:
        return min(max(float(osm_tags.get("building:levels", "0")) * 3.2, 3.5), 80.0)
    except ValueError:
        return 7.0


def stable_buildings(ways: list[tuple[list[tuple[float, float]], dict[str, str]]], limit: int) -> list[tuple[list[tuple[float, float]], dict[str, str]]]:
    """Keep a geographically even subset; dense city blocks don't overload SITL."""
    cells: dict[tuple[int, int], list[tuple[float, float, list[tuple[float, float]], dict[str, str]]]] = defaultdict(list)
    for coords, osm_tags in ways:
        xs, ys = zip(*coords)
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        cells[(round(cx / 180), round(cy / 180))].append((cx, cy, coords, osm_tags))

    selected: list[tuple[list[tuple[float, float]], dict[str, str]]] = []
    quota = max(1, math.ceil(limit / max(len(cells), 1)))
    for cell in sorted(cells):
        entries = sorted(cells[cell], key=lambda item: (abs(item[0]) + abs(item[1]), -len(item[2])))
        selected.extend((coords, osm_tags) for _, _, coords, osm_tags in entries[:quota])
    return selected[:limit]


def material(name: str, ambient: str, diffuse: str) -> str:
    return f"""<material><ambient>{ambient}</ambient><diffuse>{diffuse}</diffuse><specular>0.1 0.1 0.1 1</specular></material>"""


def box_model(name: str, x: float, y: float, z: float, sx: float, sy: float, sz: float, yaw: float, visual_material: str, collision: bool) -> str:
    collision_xml = "" if not collision else f"<collision name='collision'><geometry><box><size>{sx:.2f} {sy:.2f} {sz:.2f}</size></box></geometry></collision>"
    return f"""<model name='{name}'><static>true</static><pose>{x:.2f} {y:.2f} {z:.2f} 0 0 {yaw:.5f}</pose><link name='link'>{collision_xml}<visual name='visual'><geometry><box><size>{sx:.2f} {sy:.2f} {sz:.2f}</size></box></geometry>{visual_material}</visual></link></model>"""


def generate(root: ET.Element, output_path: Path, limit: int) -> tuple[int, int]:
    nodes = {
        node.attrib["id"]: (float(node.attrib["lat"]), float(node.attrib["lon"]))
        for node in root.findall("node")
    }
    buildings: list[tuple[list[tuple[float, float]], dict[str, str]]] = []
    roads: list[tuple[list[tuple[float, float]], dict[str, str]]] = []
    for way in root.findall("way"):
        osm_tags = tags(way)
        coords = [meters(*nodes[ref.attrib["ref"]]) for ref in way.findall("nd") if ref.attrib["ref"] in nodes]
        if len(coords) < 2:
            continue
        if "building" in osm_tags and len(coords) >= 3:
            buildings.append((coords, osm_tags))
        elif osm_tags.get("highway") in {"primary", "secondary", "tertiary"}:
            roads.append((coords, osm_tags))

    buildings = stable_buildings(buildings, limit)
    road_parts: list[str] = []
    road_material = material("road", "0.08 0.09 0.10 1", "0.12 0.13 0.14 1")
    widths = {"primary": 12.0, "secondary": 9.0, "tertiary": 7.0}
    for road_index, (coords, osm_tags) in enumerate(roads):
        for segment, ((x1, y1), (x2, y2)) in enumerate(zip(coords, coords[1:])):
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 2.0:
                continue
            road_parts.append(box_model(
                f"road_{road_index}_{segment}", (x1 + x2) / 2, (y1 + y2) / 2, 0.025,
                length, widths[osm_tags["highway"]], 0.05, math.atan2(y2 - y1, x2 - x1), road_material, False,
            ))

    building_parts: list[str] = []
    building_materials = [
        material("warm", "0.46 0.40 0.34 1", "0.65 0.56 0.46 1"),
        material("neutral", "0.40 0.42 0.43 1", "0.58 0.61 0.62 1"),
        material("brick", "0.42 0.28 0.23 1", "0.62 0.40 0.32 1"),
    ]
    for index, (coords, osm_tags) in enumerate(buildings):
        xs, ys = zip(*coords)
        sx, sy = max(xs) - min(xs), max(ys) - min(ys)
        if sx < 3 or sy < 3 or sx > 180 or sy > 180:
            continue
        height = building_height(osm_tags)
        building_parts.append(box_model(
            f"building_{index}", (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, height / 2,
            sx, sy, height, 0.0, building_materials[index % len(building_materials)], True,
        ))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"""<?xml version='1.0' encoding='UTF-8'?>
<!--
  ByteWolf Robotics Platform — Árpádföld–Mátyásföld starter world.
  Map data © OpenStreetMap contributors, available under the ODbL 1.0.
  Generated by Tools/simulation/gz/tools/build_budapest_world.py.
-->
<sdf version='1.9'>
  <world name='budapest_arpadfold_matyasfold'>
    <physics type='ode'><max_step_size>0.004</max_step_size><real_time_factor>1.0</real_time_factor><real_time_update_rate>250</real_time_update_rate></physics>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>1.83e-05 1.03e-06 -4.48e-05</magnetic_field>
    <atmosphere type='adiabatic'/>
    <scene><grid>false</grid><ambient>0.42 0.45 0.48 1</ambient><background>0.52 0.66 0.82 1</background><shadows>true</shadows><sky><clouds>true</clouds></sky></scene>
    <light name='sun' type='directional'><pose>0 0 800 0 0 0</pose><cast_shadows>true</cast_shadows><intensity>0.9</intensity><direction>-0.35 0.25 -0.9</direction><diffuse>1 0.96 0.9 1</diffuse><specular>0.2 0.2 0.2 1</specular></light>
    <spherical_coordinates><surface_model>EARTH_WGS84</surface_model><world_frame_orientation>ENU</world_frame_orientation><latitude_deg>{ORIGIN_LAT}</latitude_deg><longitude_deg>{ORIGIN_LON}</longitude_deg><elevation>150</elevation></spherical_coordinates>
    <model name='ground'><static>true</static><link name='link'><collision name='collision'><geometry><plane><normal>0 0 1</normal><size>1 1</size></plane></geometry></collision><visual name='visual'><geometry><plane><normal>0 0 1</normal><size>8000 8000</size></plane></geometry>{material('ground', '0.13 0.22 0.12 1', '0.20 0.32 0.18 1')}</visual></link></model>
    {''.join(road_parts)}
    {''.join(building_parts)}
  </world>
</sdf>
""", encoding="utf-8")
    return len(building_parts), len(road_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download fresh OpenStreetMap data, overwriting the snapshot and changing the scene",
    )
    parser.add_argument("--max-buildings", type=int, default=2800, help="maximum number of low-poly building masses")
    parser.add_argument("--osm", type=Path, default=DEFAULT_OSM_SNAPSHOT, help="OpenStreetMap snapshot to build from")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_WORLD, help="where to write the Gazebo world")
    args = parser.parse_args()
    root = osm_query(args.osm, args.refresh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    buildings, roads = generate(root, args.output, args.max_buildings)
    print(f"Generated {args.output} with {buildings} buildings and {roads} road segments.")


if __name__ == "__main__":
    main()
