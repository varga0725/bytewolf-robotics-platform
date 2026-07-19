"""Render a higher-resolution camera overlay without touching PX4's tree.

PX4's mono_cam renders 1280x960; the twin wants at least 1080p. Rather than edit
the pinned PX4 baseline, this rewrites only the resolution of PX4's read-only
mono_cam into an overlay model, exactly as the wind fixture overlays the
wind-enabled airframe. Launching with ``PX4_GZ_MODELS`` pointed at the overlay
makes the front and down cameras -- both of which include ``model://mono_cam`` --
render at the higher resolution, and nothing under the PX4 checkout changes.

It fails closed if PX4's model does not expose exactly one width and one height,
so a changed source cannot silently produce a wrong-sized camera.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re


# Dashboard preview: smaller raw frames keep the Gazebo → JPEG path interactive.
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540

_WIDTH = re.compile(r"<width>\s*(\d+)\s*</width>")
_HEIGHT = re.compile(r"<height>\s*(\d+)\s*</height>")
_LINK = re.compile(r"(<link\s+name=\"[^\"]+\">.*?</link>)", re.DOTALL)
MONO_CAM_MODEL_NAME = "mono_cam"
# PX4 spawns the airframe by an explicit file:// path under PX4_GZ_MODELS, so the
# overlay must carry the airframe models too; they include model://mono_cam,
# which resolves to the high-res overlay because it is first on the resource path.
AIRFRAME_MODEL_NAMES = ("x500_mono_cam_down", "x500_mono_cam")

_DOWN_CAMERA_WITH_LIDAR_2D = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<sdf version='1.9'>
  <model name='x500_mono_cam_down'>
    <self_collide>false</self_collide>
    <include merge='true'><uri>x500</uri></include>
    {front_camera_link}
    <joint name=\"CameraJoint\" type=\"fixed\">
      <parent>base_link</parent><child>front_camera_link</child>
    </joint>
    {down_camera_link}
    <joint name=\"DownCameraJoint\" type=\"fixed\">
      <parent>base_link</parent><child>down_camera_link</child>
    </joint>
    <include merge='true'>
      <uri>model://lidar_2d_v2</uri><pose>.12 0 .26 0 0 0</pose>
    </include>
    <joint name=\"LidarJoint\" type=\"fixed\">
      <parent>base_link</parent><child>link</child>
      <pose relative_to=\"base_link\">.12 0 .26 0 0 0</pose>
    </joint>
  </model>
</sdf>
"""


class CameraProfileError(ValueError):
    """Raised when the camera overlay cannot be rendered from the source model."""


@dataclass(frozen=True)
class CameraOverlay:
    """A rendered camera overlay and the resolution it declares."""

    models_root: Path
    width: int
    height: int


def render_high_res_mono_cam(source: str, width: int, height: int) -> str:
    """Return the mono_cam model with only its resolution raised."""
    if width <= 0 or height <= 0:
        raise CameraProfileError("Camera resolution must be positive.")
    if len(_WIDTH.findall(source)) != 1 or len(_HEIGHT.findall(source)) != 1:
        raise CameraProfileError("Source mono_cam must declare exactly one width and one height.")
    source = _WIDTH.sub(f"<width>{width}</width>", source, count=1)
    source = _HEIGHT.sub(f"<height>{height}</height>", source, count=1)
    return source


def render_named_camera_model(source: str, *, model_name: str, link_name: str, sensor_name: str = "imager") -> str:
    """Render an independently mergeable mono camera with unique SDF names.

    Gazebo merges component models into the airframe.  Therefore the two
    physical cameras must not both expose ``camera_link``: duplicate link names
    cause one sensor to disappear silently.  Renaming the model, link, and
    frame keeps the front and down streams distinct.
    """
    rendered = source.replace("<model name='mono_cam'>", f"<model name='{model_name}'>", 1)
    rendered = rendered.replace('<link name="camera_link">', f'<link name="{link_name}">', 1)
    rendered = rendered.replace('<sensor name="imager" type="camera">', f'<sensor name="{sensor_name}" type="camera">', 1)
    rendered = rendered.replace("<sensor name='imager' type='camera'>", f"<sensor name='{sensor_name}' type='camera'>", 1)
    rendered = rendered.replace("<gz_frame_id>camera_link</gz_frame_id>", f"<gz_frame_id>{link_name}</gz_frame_id>", 1)
    if rendered == source or f'<link name="{link_name}">' not in rendered:
        raise CameraProfileError("Source mono_cam does not expose the expected model and camera link names.")
    return rendered


def render_named_camera_link(source: str, *, link_name: str, sensor_name: str, pose: str) -> str:
    """Extract a uniquely named camera link for direct inclusion in the airframe."""
    rendered = render_named_camera_model(
        source, model_name="bytewolf_camera_component", link_name=link_name, sensor_name=sensor_name
    )
    match = _LINK.search(rendered)
    if match is None:
        raise CameraProfileError("Source mono_cam does not expose a camera link.")
    link = match.group(1)
    opening = f'<link name="{link_name}">'
    return link.replace(opening, f'{opening}<pose relative_to="base_link">{pose}</pose>', 1)


def create_camera_overlay(
    source_models: Path,
    models_root: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    include_lidar_2d: bool = False,
) -> CameraOverlay:
    """Write a mono_cam overlay at the requested resolution, from PX4's model."""
    source_path = source_models / MONO_CAM_MODEL_NAME / "model.sdf"
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CameraProfileError(f"Cannot read source mono_cam '{source_path}': {error.strerror}.") from error

    model = render_high_res_mono_cam(source, width, height)
    model_dir = models_root / MONO_CAM_MODEL_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.sdf").write_text(model, encoding="utf-8")
    (model_dir / "model.config").write_text(_model_config(MONO_CAM_MODEL_NAME), encoding="utf-8")

    # Copy the airframe models unchanged so PX4 can spawn one by file:// path;
    # their model://mono_cam include resolves to the overlay above.
    for airframe in AIRFRAME_MODEL_NAMES:
        airframe_source = source_models / airframe / "model.sdf"
        if not airframe_source.is_file():
            continue
        airframe_dir = models_root / airframe
        airframe_dir.mkdir(parents=True, exist_ok=True)
        (airframe_dir / "model.sdf").write_text(airframe_source.read_text(encoding="utf-8"), encoding="utf-8")
        (airframe_dir / "model.config").write_text(_model_config(airframe), encoding="utf-8")

    if include_lidar_2d:
        full_model = models_root / "x500_mono_cam_down" / "model.sdf"
        full_model.parent.mkdir(parents=True, exist_ok=True)
        full_model.write_text(
            _DOWN_CAMERA_WITH_LIDAR_2D.format(
                front_camera_link=render_named_camera_link(
                    model, link_name="front_camera_link", sensor_name="front_imager", pose=".18 0 .04 0 0 0"
                ),
                down_camera_link=render_named_camera_link(
                    model, link_name="down_camera_link", sensor_name="down_imager", pose="0 0 -.08 0 1.5707 0"
                ),
            ),
            encoding="utf-8",
        )
        full_model.with_name("model.config").write_text(_model_config("x500_mono_cam_down"), encoding="utf-8")

    return CameraOverlay(models_root=models_root, width=width, height=height)


def _model_config(name: str) -> str:
    return (
        '<?xml version="1.0"?>\n<model>\n'
        f"  <name>{name}</name>\n"
        "  <version>1.0</version>\n"
        '  <sdf version="1.9">model.sdf</sdf>\n'
        "  <description>Generated ByteWolf camera overlay; "
        "see simulation/gazebo/camera_profiles.py.</description>\n"
        "</model>\n"
    )


def main(arguments: tuple[str, ...] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render a higher-resolution mono_cam overlay from PX4's model.")
    parser.add_argument("--source-models", type=Path, required=True, help="PX4's read-only Gazebo model root.")
    parser.add_argument("--models-root", type=Path, required=True, help="Where the overlay is written.")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--include-lidar-2d", action="store_true", help="Replace the down-camera airframe overlay with a camera + 2D lidar model.")
    parsed = parser.parse_args(arguments)
    overlay = create_camera_overlay(
        parsed.source_models, parsed.models_root, width=parsed.width, height=parsed.height,
        include_lidar_2d=parsed.include_lidar_2d,
    )
    print(f"Camera overlay: {overlay.models_root}/{MONO_CAM_MODEL_NAME} at {overlay.width}x{overlay.height}")


if __name__ == "__main__":
    main()
