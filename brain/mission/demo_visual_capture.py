"""Optional gz GUI capture when xvfb-run and ffmpeg exist on the host."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def optional_screen_capture(output_path: Path, *, duration_s: float = 5.0) -> Path | None:
    """Record a short clip with xvfb-run + ffmpeg, or return None when tools are missing."""
    xvfb = shutil.which("xvfb-run")
    ffmpeg = shutil.which("ffmpeg")
    if xvfb is None or ffmpeg is None:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=duration_s + 30.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not output_path.is_file():
        return None
    return output_path
