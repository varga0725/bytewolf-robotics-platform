"""Render the simulated world from above, so a picked point lands somewhere real.

The mission map used to be an empty coordinate frame: two axes, the radius ring,
and whatever obstacle cells world memory happened to hold. With no cells it is a
black square, and an operator picking a target is picking blind.

The world itself cannot supply that picture from geometry. Baylands is a single
Fuel mesh included as one model, so projecting model poses would draw exactly two
rectangles — the park and the water — which is worse than nothing. What does work
is asking Gazebo to render it: a static, downward camera placed above the flight
area produces a true top-down view of the same world the vehicle flies in.

The camera is spawned into the *running* world over the Gazebo service API, so
this needs no world file, no model overlay, and no PX4 change. It is a scene
addition all the same:

    This is a viewing aid, not part of any evidence run. A scenario or baseline
    run must not have this camera in its world — an injected model makes the run
    something other than the world `baseline.yaml` pins.

Nothing here can command the vehicle. The module spawns a sensor, reads its
frames, and writes files the read-only dashboard serves; it opens no MAVLink and
imports no MAVSDK.

Frame convention, measured rather than assumed (see `tests/test_map_view.py` for
the numbers): with the pose this module writes, world East runs *up* the raw
image and world North runs *left*. A 90° clockwise rotation puts North up and
East right, which is what every other part of the dashboard means by a map.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import subprocess
import time


# A downward camera: rotate 90 degrees about Y so the optical axis points at the
# ground. Written as a quaternion because that is what gz.msgs.Pose takes.
_LOOK_DOWN_QUATERNION = (0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5))
# Distinct enough that `gz model --list` names it for what it is, and that no
# scenario mistakes it for part of the vehicle.
DEFAULT_MODEL_NAME = "bytewolf_map_camera"
DEFAULT_TOPIC = "bytewolf/map_camera"
DEFAULT_HORIZONTAL_FOV_RAD = 1.047
DEFAULT_PIXELS = 800
# High enough to cover the twin's 50 m radius with margin, low enough that one
# pixel still resolves a path.
DEFAULT_HEIGHT_ABOVE_GROUND_M = 158.0
DEFAULT_OUTPUT_DIR = Path("simulation/artifacts/dashboard")
CONTRACT_VERSION = "v0.1"


class MapViewError(RuntimeError):
    """The overhead view could not be produced, and no stale picture stands in."""


def camera_sdf(
    *, model_name: str, topic: str, pixels: int, horizontal_fov_rad: float
) -> str:
    """Return the SDF for one static, downward camera and nothing else.

    No collision, no visual, no inertia: it must not be able to affect the
    physics of a vehicle that may be flying while it is spawned.
    """
    return (
        '<?xml version="1.0" ?>'
        '<sdf version="1.9">'
        f'<model name="{model_name}"><static>true</static><link name="link">'
        f'<sensor name="map_imager" type="camera">'
        "<update_rate>2</update_rate><always_on>1</always_on>"
        f"<topic>{topic}</topic>"
        f"<camera><horizontal_fov>{horizontal_fov_rad}</horizontal_fov>"
        f"<image><width>{pixels}</width><height>{pixels}</height></image>"
        "<clip><near>1</near><far>2000</far></clip></camera>"
        "</sensor></link></model></sdf>"
    )


def metres_per_pixel(*, height_above_ground_m: float, horizontal_fov_rad: float, pixels: int) -> float:
    """Ground metres one pixel covers, from the pinhole geometry.

    Refuses rather than returns a default: a wrong scale silently moves every
    target the operator picks.
    """
    if height_above_ground_m <= 0 or pixels <= 0:
        raise MapViewError("The map camera needs a positive height and pixel count.")
    if not 0.0 < horizontal_fov_rad < math.pi:
        raise MapViewError("The map camera field of view must be between 0 and pi.")
    return 2.0 * height_above_ground_m * math.tan(horizontal_fov_rad / 2.0) / pixels


def _run_gz(arguments: tuple[str, ...], *, timeout_s: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env={**os.environ, "GZ_IP": "127.0.0.1"},
    )


def spawn_map_camera(
    *,
    world: str,
    east_m: float,
    north_m: float,
    height_m: float,
    model_name: str = DEFAULT_MODEL_NAME,
    topic: str = DEFAULT_TOPIC,
    pixels: int = DEFAULT_PIXELS,
    horizontal_fov_rad: float = DEFAULT_HORIZONTAL_FOV_RAD,
    run: Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]] = _run_gz,
) -> None:
    """Place the overhead camera in the running world, replacing any earlier one.

    Removing first makes a second run reposition the camera instead of failing on
    a duplicate name, which matters because the sensible place for it depends on
    where the vehicle spawned.
    """
    run(
        (
            "gz", "service", "-s", f"/world/{world}/remove",
            "--reqtype", "gz.msgs.Entity", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000", "--req", f'name: "{model_name}" type: MODEL',
        )
    )
    x, y, z, w = _LOOK_DOWN_QUATERNION
    request = (
        f'sdf: "{_escape(camera_sdf(model_name=model_name, topic=topic, pixels=pixels, horizontal_fov_rad=horizontal_fov_rad))}" '
        f"pose: {{position: {{x: {east_m}, y: {north_m}, z: {height_m}}}, "
        f"orientation: {{x: {x}, y: {y}, z: {z}, w: {w}}}}}"
    )
    result = run(
        (
            "gz", "service", "-s", f"/world/{world}/create",
            "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000", "--req", request,
        )
    )
    if "true" not in result.stdout:
        raise MapViewError(
            f"Gazebo refused the map camera in world {world!r}. Is the simulator running?"
        )


def _escape(sdf: str) -> str:
    return sdf.replace("\\", "\\\\").replace('"', '\\"')


def capture_frame(
    topic: str = DEFAULT_TOPIC,
    *,
    run: Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]] = _run_gz,
) -> tuple[bytes, int, int]:
    """Return one raw RGB frame as (pixels, width, height), or refuse.

    A frame that cannot be decoded is an error rather than a blank image: the
    dashboard showing a plausible empty park would be a lie about the world.
    """
    result = run(("gz", "topic", "-e", "-t", f"/{topic.lstrip('/')}", "--json-output", "-n", "1"))
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or "data" not in message:
            continue
        width, height = int(message.get("width", 0)), int(message.get("height", 0))
        if message.get("pixelFormatType") != "RGB_INT8" or width <= 0 or height <= 0:
            raise MapViewError("The map camera published a frame this reader cannot decode.")
        pixels = base64.b64decode(message["data"])
        if len(pixels) != width * height * 3:
            raise MapViewError("The map camera frame is truncated.")
        return pixels, width, height
    raise MapViewError(f"No frame arrived on {topic!r}.")


def _north_up_image(pixels: bytes, width: int, height: int):
    """Rotate the raw frame so North is up and East is right.

    The rotation is measured, not derived: moving the camera 40 m east shifts the
    scene 176 px down the raw image, and 40 m north shifts it 176 px right. So
    raw-up is East and raw-left is North, and one clockwise quarter turn puts
    them where a map wants them.
    """
    from PIL import Image

    return Image.frombytes("RGB", (width, height), pixels).transpose(Image.Transpose.ROTATE_270)


def write_map_view(
    *,
    output_dir: Path,
    pixels: bytes,
    width: int,
    height: int,
    ground_resolution_m: float,
    world: str,
    height_above_ground_m: float,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Write the picture and the numbers needed to read it, both atomically.

    The metadata is not decoration: without the scale and the centre, the browser
    cannot say which metre a pixel is, and a map you cannot measure is decoration.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "map-view.jpg"
    meta_path = output_dir / "map-view.json"
    image = _north_up_image(pixels, width, height)
    temporary_image = image_path.with_suffix(".jpg.tmp")
    image.save(temporary_image, format="JPEG", quality=85)
    os.replace(temporary_image, image_path)
    document = {
        "contract_version": CONTRACT_VERSION,
        "captured_at": now().isoformat().replace("+00:00", "Z"),
        "world": world,
        "source": f"gz {DEFAULT_TOPIC}",
        # The camera sits over the vehicle's spawn point, which is where the
        # dashboard's north/east grid has its origin.
        "centre_north_m": 0.0,
        "centre_east_m": 0.0,
        "metres_per_pixel": ground_resolution_m,
        "width": image.width,
        "height": image.height,
        "height_above_ground_m": height_above_ground_m,
        # Stated so the dashboard never has to guess which way is up.
        "orientation": "north_up_east_right",
    }
    temporary_meta = meta_path.with_suffix(".json.tmp")
    temporary_meta.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temporary_meta, meta_path)
    return image_path


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the simulated world from above for the mission map. Reads only; commands nothing.",
    )
    parser.add_argument("--world", default="baylands")
    parser.add_argument(
        "--spawn-east", type=float, default=205.0,
        help="Vehicle spawn X in the Gazebo world frame; the map is centred here.",
    )
    parser.add_argument("--spawn-north", type=float, default=155.0, help="Vehicle spawn Y in the world frame.")
    parser.add_argument("--ground-altitude", type=float, default=2.0, help="Ground Z at the spawn point.")
    parser.add_argument("--height", type=float, default=DEFAULT_HEIGHT_ABOVE_GROUND_M)
    parser.add_argument("--pixels", type=int, default=DEFAULT_PIXELS)
    parser.add_argument("--fov", type=float, default=DEFAULT_HORIZONTAL_FOV_RAD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--once", action="store_true", help="Render a single frame and exit instead of refreshing."
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    parsed = parse_arguments(arguments)
    resolution = metres_per_pixel(
        height_above_ground_m=parsed.height, horizontal_fov_rad=parsed.fov, pixels=parsed.pixels
    )
    spawn_map_camera(
        world=parsed.world,
        east_m=parsed.spawn_east,
        north_m=parsed.spawn_north,
        height_m=parsed.ground_altitude + parsed.height,
        pixels=parsed.pixels,
        horizontal_fov_rad=parsed.fov,
    )
    print(
        f"Map camera over the spawn point at {parsed.height:g} m: "
        f"{resolution:.3f} m/pixel, {resolution * parsed.pixels:.0f} m across."
    )
    # The scene needs a moment to render the first frame after the spawn.
    time.sleep(2.0)
    while True:
        pixels, width, height = capture_frame()
        path = write_map_view(
            output_dir=parsed.output_dir,
            pixels=pixels,
            width=width,
            height=height,
            ground_resolution_m=resolution,
            world=parsed.world,
            height_above_ground_m=parsed.height,
        )
        if parsed.once:
            print(f"Wrote {path}")
            return 0
        time.sleep(parsed.interval)


if __name__ == "__main__":
    raise SystemExit(main())
