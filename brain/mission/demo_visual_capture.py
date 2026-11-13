"""Optional gz GUI capture: in-flight recording and short post-run clips."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_MODES = frozenset({"x11", "xvfb"})
_GEOMETRY_PATTERN = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")
_DEFAULT_WM_CLASS = "gz-sim-gui"
_DEFAULT_MAX_CAPTURE_S = 600.0


def _wait_s(seconds: float) -> None:
    """Block the current thread without touching the asyncio event loop."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


@dataclass
class CaptureResult:
    status: str
    relative_path: str | None = None
    reason: str | None = None
    gz_gui_pid: int | None = None
    gz_gui_alive_at_stop: bool | None = None
    gz_gui_alive_samples: list[dict[str, object]] | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.relative_path is not None:
            payload["relative_path"] = self.relative_path
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.gz_gui_pid is not None:
            payload["gz_gui_pid"] = self.gz_gui_pid
        if self.gz_gui_alive_at_stop is not None:
            payload["gz_gui_alive_at_stop"] = self.gz_gui_alive_at_stop
        if self.gz_gui_alive_samples:
            payload["gz_gui_alive_samples"] = self.gz_gui_alive_samples
        return payload


@dataclass
class _CaptureSession:
    child_processes: list[subprocess.Popen[Any]] = field(default_factory=list)
    xvfb_display: str | None = None
    launched_gz_gui: bool = False
    launched_gz_pid: int | None = None
    launched_gz_process: subprocess.Popen[Any] | None = None
    gui_liveness_samples: list[dict[str, object]] = field(default_factory=list)
    gui_liveness_stop: threading.Event | None = None
    gui_liveness_thread: threading.Thread | None = None

    def cleanup(self) -> None:
        for process in reversed(self.child_processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.child_processes.clear()


def resolve_screen_capture_mode(cli_value: str | None) -> str | None:
    if cli_value:
        mode = cli_value.strip().lower()
        return mode if mode in _VALID_MODES else None
    env = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE", "").strip().lower()
    if env in _VALID_MODES:
        return env
    if env:
        return None
    return "xvfb"


@dataclass
class DemoFlightScreenCapture:
    """Record from before arm until ``stop()`` with a hard duration cap."""

    output_path: Path
    mode: str
    max_duration_s: float = _DEFAULT_MAX_CAPTURE_S
    evidence_directory: Path | None = None

    _session: _CaptureSession = field(default_factory=_CaptureSession, init=False)
    _ffmpeg: subprocess.Popen[Any] | None = field(default=None, init=False)
    _started_at: float | None = field(default=None, init=False)
    _result: CaptureResult | None = field(default=None, init=False)

    def start(self) -> CaptureResult:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return CaptureResult("skipped", reason="ffmpeg not installed")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "xvfb":
            prepared = self._prepare_xvfb_surface()
        else:
            prepared = self._prepare_x11_surface()
        if prepared is None:
            return CaptureResult("skipped", reason="capture surface unavailable")
        region, display = prepared
        width, height, x_offset, y_offset = region
        grab_input = f"{display}+{x_offset},{y_offset}"
        command = [
            ffmpeg,
            "-y",
            "-f",
            "x11grab",
            "-framerate",
            "15",
            "-video_size",
            f"{width}x{height}",
            "-i",
            grab_input,
            "-c:v",
            "libx264",
            str(self.output_path),
        ]
        try:
            self._ffmpeg = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            return CaptureResult("failed", reason=str(error))
        self._started_at = time.monotonic()
        self._result = CaptureResult("recording")
        self._start_gui_liveness_sampling()
        return self._result

    def stop(self) -> CaptureResult:
        if self._result is not None and self._result.status in {"skipped", "failed"}:
            self._session.cleanup()
            return self._result
        self._stop_gui_liveness_sampling()
        if self._ffmpeg is not None and self._ffmpeg.poll() is None:
            if self._ffmpeg.stdin is not None:
                try:
                    self._ffmpeg.stdin.write(b"q")
                    self._ffmpeg.stdin.flush()
                except OSError:
                    self._ffmpeg.terminate()
            try:
                self._ffmpeg.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                self._ffmpeg.kill()
        gui_process = self._session.launched_gz_process
        gui_pid = self._session.launched_gz_pid
        gui_alive = _child_process_is_alive(gui_process)
        samples = list(self._session.gui_liveness_samples)
        should_have_gui = _should_launch_gz_gui(self.mode)
        launched_gui = self._session.launched_gz_gui and gui_process is not None
        self._session.cleanup()
        elapsed = 0.0 if self._started_at is None else time.monotonic() - self._started_at
        common = {
            "gz_gui_pid": gui_pid,
            "gz_gui_alive_at_stop": gui_alive,
            "gz_gui_alive_samples": samples or None,
        }
        if elapsed > self.max_duration_s:
            return CaptureResult("failed", reason="capture exceeded max duration before stop", **common)
        has_video = self.output_path.is_file() and self.output_path.stat().st_size > 0
        relative = (
            str(self.output_path.relative_to(self.evidence_directory))
            if has_video and self.evidence_directory
            else (self.output_path.name if has_video else None)
        )
        if should_have_gui and not launched_gui:
            if has_video:
                return CaptureResult(
                    "captured_no_gui",
                    relative_path=relative,
                    reason="screen recording ran without a gz sim -g client",
                    **common,
                )
            return CaptureResult("failed", reason="gz sim -g was not launched", **common)
        if should_have_gui and not gui_alive:
            return CaptureResult("failed", reason="gz sim -g was not alive at capture stop", **common)
        if should_have_gui and samples and not any(sample.get("alive") for sample in samples):
            return CaptureResult("failed", reason="gz sim -g was never alive during capture", **common)
        if has_video and not should_have_gui:
            return CaptureResult(
                "captured_no_gui",
                relative_path=relative,
                reason="gz sim -g launch disabled for this capture mode",
                **common,
            )
        if has_video:
            return CaptureResult("captured", relative_path=relative, **common)
        return CaptureResult("failed", reason="ffmpeg did not produce a video file", **common)

    def _start_gui_liveness_sampling(self) -> None:
        if not _should_launch_gz_gui(self.mode):
            return
        stop = threading.Event()
        self._session.gui_liveness_stop = stop

        process = self._session.launched_gz_process
        if process is not None:
            self._session.gui_liveness_samples.append(
                {"elapsed_s": 0.0, "alive": _child_process_is_alive(process)}
            )

        def sample_loop() -> None:
            started = self._started_at or time.monotonic()
            while not stop.wait(2.0):
                process = self._session.launched_gz_process
                self._session.gui_liveness_samples.append(
                    {
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "alive": _child_process_is_alive(process),
                    }
                )

        thread = threading.Thread(target=sample_loop, name="gz-gui-liveness", daemon=True)
        self._session.gui_liveness_thread = thread
        thread.start()

    def _stop_gui_liveness_sampling(self) -> None:
        stop = self._session.gui_liveness_stop
        thread = self._session.gui_liveness_thread
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._session.gui_liveness_stop = None
        self._session.gui_liveness_thread = None

    def _prepare_x11_surface(self) -> tuple[tuple[int, int, int, int], str] | None:
        display = os.environ.get("DISPLAY")
        if not display:
            return None
        if _should_launch_gz_gui("x11"):
            process = _launch_gz_gui_client(display, attach_to_running_server=True)
            if process is None:
                return None
            self._session.child_processes.append(process)
            self._session.launched_gz_gui = True
            self._session.launched_gz_pid = process.pid
            self._session.launched_gz_process = process
            _wait_s(_gz_gui_attach_delay_s())
        region = _resolve_window_region(self._session, display=display)
        if region is None:
            return None
        return region, display

    def _prepare_xvfb_surface(self) -> tuple[tuple[int, int, int, int], str] | None:
        xvfb = shutil.which("Xvfb")
        if xvfb is None:
            return None
        display_number = int(os.environ.get("BYTEWOLF_DEMO_XVFB_DISPLAY_NUM", "198"))
        display = f":{display_number}"
        try:
            xvfb_process = subprocess.Popen(
                [xvfb, display, "-screen", "0", "1280x720x24", "-ac"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        self._session.child_processes.append(xvfb_process)
        self._session.xvfb_display = display
        _wait_s(0.5)
        if _should_launch_gz_gui("xvfb"):
            process = _launch_gz_gui_client(display, attach_to_running_server=True)
            if process is None:
                return None
            self._session.child_processes.append(process)
            self._session.launched_gz_gui = True
            self._session.launched_gz_pid = process.pid
            self._session.launched_gz_process = process
            _wait_s(_gz_gui_attach_delay_s())
        region = _resolve_window_region(self._session, display=display)
        if region is None:
            geometry = os.environ.get(
                "BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY",
                "1280x720+0+0",
            )
            region = _parse_geometry(geometry) if geometry else None
        if region is None:
            return None
        return region, display


def optional_screen_capture(
    output_path: Path,
    *,
    duration_s: float = 5.0,
    mode: str | None = None,
    window_name: str | None = None,
    geometry: str | None = None,
) -> Path | None:
    """Legacy short clip helper (post-run); prefer :class:`DemoFlightScreenCapture` in-flight."""
    _ = (window_name, geometry)
    resolved = mode if mode in _VALID_MODES else resolve_screen_capture_mode(mode)
    if resolved is None:
        return None
    capture = DemoFlightScreenCapture(output_path, mode=resolved, max_duration_s=duration_s + 5.0)
    start = capture.start()
    if start.status != "recording":
        return None
    _wait_s(duration_s)
    finished = capture.stop()
    if finished.status == "captured":
        return output_path
    return None


def _should_launch_gz_gui(mode: str) -> bool:
    override = os.environ.get("BYTEWOLF_DEMO_LAUNCH_GZ_GUI")
    if override is not None:
        return override.strip().lower() not in {"0", "false", "no"}
    return mode == "xvfb"


def _gz_gui_attach_delay_s() -> float:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_GUI_DELAY_S", "5")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


def _gz_transport_env(display: str, *, attach_to_running_server: bool) -> dict[str, str] | None:
    env = dict(os.environ)
    env["DISPLAY"] = display
    gz_ip = env.get("GZ_IP", "127.0.0.1").strip()
    if gz_ip != "127.0.0.1":
        return None
    env["GZ_IP"] = "127.0.0.1"
    if attach_to_running_server:
        env.setdefault("PX4_GZ_STANDALONE", "1")
    return env


def _launch_gz_gui_client(display: str, *, attach_to_running_server: bool = False) -> subprocess.Popen[Any] | None:
    gz = shutil.which("gz")
    if gz is None:
        return None
    env = _gz_transport_env(display, attach_to_running_server=attach_to_running_server)
    if env is None:
        return None
    try:
        return subprocess.Popen(
            [gz, "sim", "-g"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def _child_process_is_alive(process: subprocess.Popen[Any] | None) -> bool:
    return process is not None and process.poll() is None


def _resolve_window_region(session: _CaptureSession, *, display: str) -> tuple[int, int, int, int] | None:
    geometry = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY")
    parsed = _parse_geometry(geometry) if geometry else None
    if parsed is not None:
        return parsed
    if session.launched_gz_pid is not None:
        region = _locate_window_by_pid(session.launched_gz_pid, display=display)
        if region is not None:
            return region
    pid_raw = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_GZ_PID")
    if pid_raw:
        region = _locate_window_by_pid(int(pid_raw), display=display)
        if region is not None:
            return region
    title = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_WINDOW_TITLE")
    if title:
        region = _locate_window_by_exact_title(title, display=display)
        if region is not None:
            return region
    wm_class = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_WM_CLASS", _DEFAULT_WM_CLASS)
    return _locate_window_by_class(wm_class, display=display)


def _parse_geometry(value: str) -> tuple[int, int, int, int] | None:
    match = _GEOMETRY_PATTERN.match(value.strip())
    if not match:
        return None
    width, height, x_offset, y_offset = (int(match.group(index)) for index in range(1, 5))
    if width <= 0 or height <= 0:
        return None
    return width, height, x_offset, y_offset


def _locate_window_by_pid(pid: int, *, display: str) -> tuple[int, int, int, int] | None:
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return None
    try:
        search = subprocess.run(
            [xdotool, "search", "--pid", str(pid), "--onlyviable", "--limit", "1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            env=_display_env(display),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if search.returncode != 0 or not search.stdout.strip():
        return None
    return _window_geometry_from_id(search.stdout.strip().splitlines()[0], display=display)


def _locate_window_by_exact_title(title: str, *, display: str) -> tuple[int, int, int, int] | None:
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return None
    try:
        search = subprocess.run(
            [xdotool, "search", "--name", title, "--onlyviable", "--matchmode", "3", "--limit", "1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            env=_display_env(display),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if search.returncode != 0 or not search.stdout.strip():
        return None
    return _window_geometry_from_id(search.stdout.strip().splitlines()[0], display=display)


def _locate_window_by_class(
    wm_class: str,
    *,
    display: str,
) -> tuple[int, int, int, int] | None:
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return None
    env = _display_env(display)
    try:
        search = subprocess.run(
            [xdotool, "search", "--class", wm_class, "--onlyviable", "--limit", "1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if search.returncode != 0 or not search.stdout.strip():
        return None
    window_id = search.stdout.strip().splitlines()[0]
    return _window_geometry_from_id(window_id, display=display)


def _window_geometry_from_id(
    window_id: str,
    *,
    display: str,
) -> tuple[int, int, int, int] | None:
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return None
    try:
        geo = subprocess.run(
            [xdotool, "getwindowgeometry", "--shell", window_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            env=_display_env(display),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if geo.returncode != 0:
        return None
    values: dict[str, str] = {}
    for line in geo.stdout.splitlines():
        if "=" in line:
            key, raw = line.split("=", 1)
            values[key.strip()] = raw.strip()
    try:
        return (
            int(values["WIDTH"]),
            int(values["HEIGHT"]),
            int(values["X"]),
            int(values["Y"]),
        )
    except (KeyError, ValueError):
        return None


def _display_env(display: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if display is not None:
        env["DISPLAY"] = display
    return env
