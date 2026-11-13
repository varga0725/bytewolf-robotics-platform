"""Optional gz GUI capture via existing X11 display or xvfb-run."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_VALID_MODES = frozenset({"x11", "xvfb"})
_GEOMETRY_PATTERN = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")
_DEFAULT_WINDOW_NAME = "Gazebo"


@dataclass
class _CaptureSession:
    """Tracks windows/subprocesses this module started so cleanup stays scoped."""

    opened_window_ids: list[str] = field(default_factory=list)
    child_processes: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def cleanup(self) -> None:
        xdotool = shutil.which("xdotool")
        for window_id in self.opened_window_ids:
            if xdotool is None:
                continue
            subprocess.run(
                [xdotool, "windowclose", window_id],
                check=False,
                capture_output=True,
                text=True,
            )
        for process in self.child_processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()


def resolve_screen_capture_mode(cli_value: str | None) -> str | None:
    """Return ``x11`` or ``xvfb``; CLI overrides ``BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE``.

    Invalid values return ``None``. When neither CLI nor env is set, default is ``xvfb``
    (headless-friendly) so acceptance runs keep prior behavior unless configured otherwise.
    """
    if cli_value:
        mode = cli_value.strip().lower()
        return mode if mode in _VALID_MODES else None
    env = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE", "").strip().lower()
    if env in _VALID_MODES:
        return env
    if env:
        return None
    return "xvfb"


def optional_screen_capture(
    output_path: Path,
    *,
    duration_s: float = 5.0,
    mode: str | None = None,
    window_name: str | None = None,
    geometry: str | None = None,
) -> Path | None:
    """Record a short gz GUI clip, or return ``None`` when tools or geometry are missing."""
    resolved = mode if mode in _VALID_MODES else resolve_screen_capture_mode(mode)
    if resolved is None:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    session = _CaptureSession()
    try:
        if resolved == "xvfb":
            return _capture_xvfb(ffmpeg, output_path, duration_s=duration_s, session=session)
        return _capture_x11(
            ffmpeg,
            output_path,
            duration_s=duration_s,
            window_name=window_name,
            geometry=geometry,
            session=session,
        )
    finally:
        session.cleanup()


def _capture_xvfb(
    ffmpeg: str,
    output_path: Path,
    *,
    duration_s: float,
    session: _CaptureSession,
) -> Path | None:
    xvfb = shutil.which("xvfb-run")
    if xvfb is None:
        return None
    display = ":199"
    command = [
        xvfb,
        "-a",
        "-s",
        "-screen 0 1280x720x24",
        ffmpeg,
        "-y",
        "-f",
        "x11grab",
        "-video_size",
        "1280x720",
        "-i",
        display,
        "-t",
        str(duration_s),
        str(output_path),
    ]
    return _run_ffmpeg_command(command, output_path, timeout_s=duration_s + 30.0)


def _capture_x11(
    ffmpeg: str,
    output_path: Path,
    *,
    duration_s: float,
    window_name: str | None,
    geometry: str | None,
    session: _CaptureSession,
) -> Path | None:
    display = os.environ.get("DISPLAY")
    if not display:
        return None
    region = _resolve_window_region(
        window_name=window_name or os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_WINDOW_NAME", _DEFAULT_WINDOW_NAME),
        geometry=geometry or os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY"),
        session=session,
    )
    if region is None:
        return None
    width, height, x_offset, y_offset = region
    grab_input = f"{display}+{x_offset},{y_offset}"
    command = [
        ffmpeg,
        "-y",
        "-f",
        "x11grab",
        "-video_size",
        f"{width}x{height}",
        "-i",
        grab_input,
        "-t",
        str(duration_s),
        str(output_path),
    ]
    return _run_ffmpeg_command(command, output_path, timeout_s=duration_s + 30.0)


def _resolve_window_region(
    *,
    window_name: str,
    geometry: str | None,
    session: _CaptureSession,
) -> tuple[int, int, int, int] | None:
    parsed = _parse_geometry(geometry) if geometry else None
    if parsed is not None:
        return parsed
    located = _locate_window_geometry(window_name)
    if located is not None:
        return located
    return None


def _parse_geometry(value: str) -> tuple[int, int, int, int] | None:
    match = _GEOMETRY_PATTERN.match(value.strip())
    if not match:
        return None
    width, height, x_offset, y_offset = (int(match.group(index)) for index in range(1, 5))
    if width <= 0 or height <= 0:
        return None
    return width, height, x_offset, y_offset


def _locate_window_geometry(window_name: str) -> tuple[int, int, int, int] | None:
    xdotool = shutil.which("xdotool")
    if xdotool is not None:
        try:
            search = subprocess.run(
                [xdotool, "search", "--name", window_name, "--onlyviable", "--limit", "1"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            search = None
        if search is not None and search.returncode == 0 and search.stdout.strip():
            window_id = search.stdout.strip().splitlines()[0]
            try:
                geo = subprocess.run(
                    [xdotool, "getwindowgeometry", "--shell", window_id],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
            except (OSError, subprocess.TimeoutExpired):
                geo = None
            if geo is not None and geo.returncode == 0:
                values = {}
                for line in geo.stdout.splitlines():
                    if "=" in line:
                        key, raw = line.split("=", 1)
                        values[key.strip()] = raw.strip()
                width = values.get("WIDTH")
                height = values.get("HEIGHT")
                x_offset = values.get("X")
                y_offset = values.get("Y")
                if None not in (width, height, x_offset, y_offset):
                    return int(width), int(height), int(x_offset), int(y_offset)

    xwininfo = shutil.which("xwininfo")
    if xwininfo is not None:
        try:
            tree = subprocess.run(
                [xwininfo, "-root", "-tree"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if tree.returncode != 0:
            return None
        window_id: str | None = None
        needle = window_name.casefold()
        for line in tree.stdout.splitlines():
            if needle not in line.casefold():
                continue
            match = re.search(r"(0x[0-9a-fA-F]+)", line)
            if match:
                window_id = match.group(1)
                break
        if window_id is None:
            return None
        try:
            info = subprocess.run(
                [xwininfo, "-id", window_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if info.returncode != 0:
            return None
        numbers = _parse_xwininfo_geometry(info.stdout)
        if numbers is not None:
            return numbers
    return None


def _parse_xwininfo_geometry(text: str) -> tuple[int, int, int, int] | None:
    abs_x = abs_y = width = height = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Absolute upper-left X:"):
            abs_x = int(stripped.split(":")[1].strip())
        elif stripped.startswith("Absolute upper-left Y:"):
            abs_y = int(stripped.split(":")[1].strip())
        elif stripped.startswith("Width:"):
            width = int(stripped.split(":")[1].strip())
        elif stripped.startswith("Height:"):
            height = int(stripped.split(":")[1].strip())
    if None in (abs_x, abs_y, width, height) or width <= 0 or height <= 0:
        return None
    return width, height, abs_x, abs_y


def _run_ffmpeg_command(
    command: list[str],
    output_path: Path,
    *,
    timeout_s: float,
) -> Path | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not output_path.is_file():
        return None
    return output_path
