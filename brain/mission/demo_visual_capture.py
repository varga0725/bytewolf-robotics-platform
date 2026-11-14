"""Optional gz GUI capture: in-flight recording and short post-run clips."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

_VALID_MODES = frozenset({"x11", "xvfb"})
_GEOMETRY_PATTERN = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")
_DEFAULT_WM_CLASS = "gz-sim-gui"
_DEFAULT_MAX_CAPTURE_S = 600.0
_DEFAULT_XVFB_SCREEN = "1920x1080x24"
_XVFB_SCREEN_PATTERN = re.compile(r"^(\d+)x(\d+)x\d+$")
_GZ_GUI_STARTUP_GRACE_S = 10.0


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
    gz_gui_log_relative_path: str | None = None
    gz_gui_exit_code: int | None = None
    screenshot_relative_path: str | None = None
    screenshot_gui_alive: bool | None = None
    screenshot_non_uniform: bool | None = None
    gz_gui_extra_env_rejected: list[str] | None = None
    gz_gui_args_rejected: list[str] | None = None
    gui_env_libgl_software: bool | None = None
    gui_retry_render_engine: str | None = None
    demo_gui_config_relative_path: str | None = None

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
        if self.gz_gui_log_relative_path is not None:
            payload["gz_gui_log_relative_path"] = self.gz_gui_log_relative_path
        if self.gz_gui_exit_code is not None:
            payload["gz_gui_exit_code"] = self.gz_gui_exit_code
        if self.screenshot_relative_path is not None:
            payload["screenshot_relative_path"] = self.screenshot_relative_path
        if self.screenshot_gui_alive is not None:
            payload["screenshot_gui_alive"] = self.screenshot_gui_alive
        if self.screenshot_non_uniform is not None:
            payload["screenshot_non_uniform"] = self.screenshot_non_uniform
        if self.gz_gui_extra_env_rejected:
            payload["gz_gui_extra_env_rejected"] = self.gz_gui_extra_env_rejected
        if self.gz_gui_args_rejected:
            payload["gz_gui_args_rejected"] = self.gz_gui_args_rejected
        if self.gui_env_libgl_software is not None:
            payload["gui_env_libgl_software"] = self.gui_env_libgl_software
        if self.gui_retry_render_engine is not None:
            payload["gui_retry_render_engine"] = self.gui_retry_render_engine
        if self.demo_gui_config_relative_path is not None:
            payload["demo_gui_config_relative_path"] = self.demo_gui_config_relative_path
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
    grab_region: tuple[int, int, int, int] | None = None
    grab_display: str | None = None
    gz_gui_log_file: TextIO | None = None
    gz_gui_log_path: Path | None = None
    screenshot_path: Path | None = None
    screenshot_relative_path: str | None = None
    screenshot_gui_alive: bool | None = None
    screenshot_non_uniform: bool | None = None
    screenshot_thread: threading.Thread | None = None
    screenshot_stop: threading.Event | None = None
    gz_gui_extra_env_rejected: list[str] = field(default_factory=list)
    gz_gui_args_rejected: list[str] = field(default_factory=list)
    gui_env_libgl_software: bool | None = None
    gui_retry_render_engine: str | None = None
    capture_started_epoch: float | None = None
    demo_gui_config_path: str | None = None

    def cleanup(self) -> None:
        for process in reversed(self.child_processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.child_processes.clear()
        if self.gz_gui_log_file is not None:
            try:
                self.gz_gui_log_file.close()
            except OSError:
                pass
            self.gz_gui_log_file = None


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
    artifact_directory: Path | None = None
    screenshot_path: Path | None = None

    _session: _CaptureSession = field(default_factory=_CaptureSession, init=False)
    _ffmpeg: subprocess.Popen[Any] | None = field(default=None, init=False)
    _started_at: float | None = field(default=None, init=False)
    _result: CaptureResult | None = field(default=None, init=False)

    @property
    def capture_started_epoch(self) -> float | None:
        return self._session.capture_started_epoch

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
        self._session.grab_region = region
        self._session.grab_display = display
        if self.screenshot_path is not None:
            self._session.screenshot_path = self.screenshot_path
        grab_input = f"{display}+{x_offset},{y_offset}"
        command = [
            ffmpeg,
            "-y",
            "-draw_mouse",
            "0",
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
        self._session.capture_started_epoch = time.time()
        self._result = CaptureResult("recording")
        self._start_gui_liveness_sampling()
        self._schedule_midflight_screenshot()
        return self._result

    def stop(self) -> CaptureResult:
        if self._result is not None and self._result.status in {"skipped", "failed"}:
            self._session.cleanup()
            return self._result
        self._stop_gui_liveness_sampling()
        self._stop_screenshot_thread()
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
        gui_exit_code = gui_process.poll() if gui_process is not None else None
        gui_alive = _child_process_is_alive(gui_process)
        samples = list(self._session.gui_liveness_samples)
        log_relative = _artifact_relative_path(self.artifact_directory, self._session.gz_gui_log_path)
        screenshot_relative = self._session.screenshot_relative_path
        screenshot_valid = (
            screenshot_relative is not None
            and self._session.screenshot_gui_alive is True
            and self._session.screenshot_non_uniform is True
        )
        should_have_gui = _should_launch_gz_gui(self.mode)
        launched_gui = self._session.launched_gz_gui and gui_process is not None
        self._session.cleanup()
        elapsed = 0.0 if self._started_at is None else time.monotonic() - self._started_at
        common = {
            "gz_gui_pid": gui_pid,
            "gz_gui_alive_at_stop": gui_alive,
            "gz_gui_alive_samples": samples or None,
            "gz_gui_log_relative_path": log_relative,
            "gz_gui_exit_code": gui_exit_code,
            "screenshot_relative_path": screenshot_relative if screenshot_valid else None,
            "screenshot_gui_alive": self._session.screenshot_gui_alive,
            "screenshot_non_uniform": self._session.screenshot_non_uniform,
            "gz_gui_extra_env_rejected": self._session.gz_gui_extra_env_rejected or None,
            "gz_gui_args_rejected": self._session.gz_gui_args_rejected or None,
            "gui_env_libgl_software": self._session.gui_env_libgl_software,
            "gui_retry_render_engine": self._session.gui_retry_render_engine,
            "demo_gui_config_relative_path": _demo_gui_config_relative(self._session.demo_gui_config_path),
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

    def _schedule_midflight_screenshot(self) -> None:
        if self._session.screenshot_path is None:
            return
        if os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_SCREENSHOT", "1").strip().lower() in {
            "0",
            "false",
            "no",
        }:
            return

        capture = self

        stop = threading.Event()
        self._session.screenshot_stop = stop

        def worker() -> None:
            try:
                delay = float(os.environ.get("BYTEWOLF_DEMO_SCREENSHOT_DELAY_S", "30"))
            except ValueError:
                delay = 30.0
            if stop.wait(max(0.0, delay)):
                return
            capture._capture_screenshot_frame()

        thread = threading.Thread(target=worker, name="demo-screenshot", daemon=True)
        self._session.screenshot_thread = thread
        thread.start()

    def _stop_screenshot_thread(self) -> None:
        stop = self._session.screenshot_stop
        thread = self._session.screenshot_thread
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._session.screenshot_stop = None
        self._session.screenshot_thread = None

    def _capture_screenshot_frame(self) -> None:
        if self._session.screenshot_stop is not None and self._session.screenshot_stop.is_set():
            return
        gui_process = self._session.launched_gz_process
        gui_alive = _child_process_is_alive(gui_process)
        self._session.screenshot_gui_alive = gui_alive
        if not gui_alive:
            return
        ffmpeg = shutil.which("ffmpeg")
        region = self._session.grab_region
        display = self._session.grab_display
        output = self._session.screenshot_path
        if ffmpeg is None or region is None or display is None or output is None:
            return
        width, height, x_offset, y_offset = region
        command = [
            ffmpeg,
            "-y",
            "-f",
            "x11grab",
            "-video_size",
            f"{width}x{height}",
            "-i",
            f"{display}+{x_offset},{y_offset}",
            "-frames:v",
            "1",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            self._session.screenshot_non_uniform = False
            return
        self._session.screenshot_non_uniform = _png_has_non_uniform_content(output)
        if not self._session.screenshot_non_uniform:
            output.unlink(missing_ok=True)
            return
        if self.evidence_directory is not None:
            try:
                self._session.screenshot_relative_path = str(output.relative_to(self.evidence_directory))
            except ValueError:
                self._session.screenshot_relative_path = output.name
        else:
            self._session.screenshot_relative_path = output.name

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
            process = _launch_gz_gui_with_startup_retry(
                display,
                attach_to_running_server=True,
                artifact_directory=self.artifact_directory,
                session=self._session,
            )
            if process is None:
                return None
            self._session.launched_gz_gui = True
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
        screen_spec = os.environ.get("BYTEWOLF_DEMO_XVFB_SCREEN_SIZE", _DEFAULT_XVFB_SCREEN).strip()
        try:
            xvfb_process = subprocess.Popen(
                [
                    xvfb,
                    display,
                    "-screen",
                    "0",
                    screen_spec,
                    "+extension",
                    "GLX",
                    "+render",
                    "-noreset",
                    "-ac",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        self._session.child_processes.append(xvfb_process)
        self._session.xvfb_display = display
        _wait_s(0.5)
        if _should_launch_gz_gui("xvfb"):
            process = _launch_gz_gui_with_startup_retry(
                display,
                attach_to_running_server=True,
                artifact_directory=self.artifact_directory,
                session=self._session,
            )
            if process is None:
                return None
            self._session.launched_gz_gui = True
            _wait_s(_gz_gui_attach_delay_s())
        region = _resolve_xvfb_grab_region(display=display)
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


def _gz_gui_startup_grace_s() -> float:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_GUI_STARTUP_GRACE_S")
    if raw is None:
        return _GZ_GUI_STARTUP_GRACE_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _GZ_GUI_STARTUP_GRACE_S


def _xvfb_screen_dimensions() -> tuple[int, int]:
    raw = os.environ.get("BYTEWOLF_DEMO_XVFB_SCREEN_SIZE", _DEFAULT_XVFB_SCREEN).strip()
    match = _XVFB_SCREEN_PATTERN.match(raw)
    if match is None:
        return 1920, 1080
    width, height = (int(match.group(1)), int(match.group(2)))
    if width <= 0 or height <= 0:
        return 1920, 1080
    return width, height


def _resolve_xvfb_grab_region(*, display: str) -> tuple[int, int, int, int] | None:
    """Default Xvfb capture covers the full private screen (not a partial WM geometry)."""
    _ = display
    geometry = os.environ.get("BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY")
    if geometry:
        return _parse_geometry(geometry)
    width, height = _xvfb_screen_dimensions()
    return width, height, 0, 0


def _libgl_software_enabled(extra: dict[str, str]) -> bool:
    value = extra.get("LIBGL_ALWAYS_SOFTWARE")
    if value is None:
        return False
    return value.strip().lower() not in {"0", "false", "no", ""}


def _replace_render_engine_args(args: list[str], engine: str) -> list[str]:
    filtered: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == "--render-engine" and index + 1 < len(args):
            index += 2
            continue
        filtered.append(args[index])
        index += 1
    return [*filtered, "--render-engine", engine]


def _gui_survived_startup(process: subprocess.Popen[Any], grace_s: float) -> bool:
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    return process.poll() is None


def _detach_gui_child(session: _CaptureSession, process: subprocess.Popen[Any]) -> None:
    if process in session.child_processes:
        session.child_processes.remove(process)
    if process.poll() is None:
        process.kill()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            pass
    if session.launched_gz_process is process:
        session.launched_gz_process = None
        session.launched_gz_pid = None


def _register_gui_child(session: _CaptureSession, process: subprocess.Popen[Any]) -> None:
    session.child_processes.append(process)
    session.launched_gz_pid = process.pid
    session.launched_gz_process = process


def _launch_gz_gui_with_startup_retry(
    display: str,
    *,
    attach_to_running_server: bool,
    artifact_directory: Path | None,
    session: _CaptureSession,
) -> subprocess.Popen[Any] | None:
    grace_s = _gz_gui_startup_grace_s()
    process = _launch_gz_gui_client(
        display,
        attach_to_running_server=attach_to_running_server,
        artifact_directory=artifact_directory,
        session=session,
    )
    if process is None:
        return None
    _register_gui_child(session, process)
    if _gui_survived_startup(process, grace_s):
        return process
    _detach_gui_child(session, process)
    session.gui_retry_render_engine = "ogre"
    retry = _launch_gz_gui_client(
        display,
        attach_to_running_server=attach_to_running_server,
        artifact_directory=artifact_directory,
        session=session,
        force_render_engine="ogre",
    )
    if retry is None:
        return None
    _register_gui_child(session, retry)
    return retry


def _artifact_relative_path(artifact_directory: Path | None, path: Path | None) -> str | None:
    if artifact_directory is None or path is None:
        return None
    try:
        return str(path.relative_to(artifact_directory))
    except ValueError:
        return path.name


_GZ_GUI_ENV_ALLOWLIST = frozenset(
    {
        "LIBGL_ALWAYS_SOFTWARE",
        "GALLIUM_DRIVER",
        "MESA_LOADER_DRIVER_OVERRIDE",
        "MESA_GL_VERSION_OVERRIDE",
        "MESA_GLSL_VERSION_OVERRIDE",
        "QT_QPA_PLATFORM",
        "QT_XCB_GL_INTEGRATION",
        "QT_QUICK_BACKEND",
        "QT_OPENGL",
        "GZ_GUI_RESOURCE_PATH",
        "OGRE_RTT_MODE",
    }
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _demo_gui_config_relative(path: str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return str(candidate.relative_to(_repository_root()))
    except ValueError:
        return candidate.name


def _resolved_demo_gui_launch_args(extra_args: list[str]) -> list[str]:
    if any(token == "--gui-config" for token in extra_args):
        return []
    from brain.mission.demo_gz_scene import default_demo_gui_config_path, demo_gui_config_enabled

    if not demo_gui_config_enabled():
        return []
    config_path = default_demo_gui_config_path()
    if not config_path.is_file():
        return []
    return ["--gui-config", str(config_path)]


def _parse_gz_gui_extra_env() -> tuple[dict[str, str], list[str]]:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_GUI_EXTRA_ENV", "").strip()
    if not raw:
        return {}, []
    rejected: list[str] = []
    pairs: dict[str, str] = {}
    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}, ["BYTEWOLF_DEMO_GZ_GUI_EXTRA_ENV"]
        if not isinstance(loaded, dict):
            return {}, ["BYTEWOLF_DEMO_GZ_GUI_EXTRA_ENV"]
        for key, value in loaded.items():
            key_s = str(key)
            if key_s in _GZ_GUI_ENV_ALLOWLIST:
                pairs[key_s] = str(value)
            else:
                rejected.append(key_s)
        return pairs, rejected
    for token in raw.split(";"):
        piece = token.strip()
        if not piece or "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if key in _GZ_GUI_ENV_ALLOWLIST:
            pairs[key] = value.strip()
        else:
            rejected.append(key)
    return pairs, rejected


def _validated_gz_gui_args() -> tuple[list[str], list[str]]:
    raw = os.environ.get("BYTEWOLF_DEMO_GZ_GUI_ARGS", "").strip()
    if not raw:
        return [], []
    tokens = shlex.split(raw)
    allowed: list[str] = []
    rejected: list[str] = []
    repo_root = _repository_root().resolve()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--render-engine" and index + 1 < len(tokens) and tokens[index + 1] in {"ogre", "ogre2"}:
            allowed.extend([token, tokens[index + 1]])
            index += 2
            continue
        if token in {"-v", "--verbose"} and index + 1 < len(tokens):
            level = tokens[index + 1]
            if level.isdigit() and 0 <= int(level) <= 4:
                allowed.extend([token, level])
                index += 2
                continue
        if token == "--gui-config" and index + 1 < len(tokens):
            candidate = Path(tokens[index + 1])
            if not candidate.is_absolute():
                candidate = (repo_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            try:
                candidate.relative_to(repo_root)
                allowed.extend(["--gui-config", str(candidate)])
                index += 2
                continue
            except ValueError:
                rejected.append(token)
                index += 2
                continue
        rejected.append(token)
        index += 1
    return allowed, rejected


def _png_has_non_uniform_content(path: Path) -> bool:
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return path.stat().st_size > 512
    try:
        image = Image.open(path).convert("L")
    except OSError:
        return False
    stats = ImageStat.Stat(image)
    variance = stats.var[0]
    mean_luma = stats.mean[0]
    if variance < 8.0:
        return False
    if mean_luma < 2.0 or mean_luma > 253.0:
        return False
    return True


def _gz_transport_env(
    display: str,
    *,
    attach_to_running_server: bool,
    session: _CaptureSession | None = None,
) -> dict[str, str] | None:
    extra, rejected_env = _parse_gz_gui_extra_env()
    libgl_software = _libgl_software_enabled(extra)
    if session is not None:
        session.gz_gui_extra_env_rejected = rejected_env
        session.gui_env_libgl_software = libgl_software
    env = dict(os.environ)
    if "LIBGL_ALWAYS_SOFTWARE" not in extra:
        env.pop("LIBGL_ALWAYS_SOFTWARE", None)
    gz_ip = env.get("GZ_IP", "127.0.0.1").strip()
    if gz_ip != "127.0.0.1":
        return None
    if attach_to_running_server:
        env.setdefault("PX4_GZ_STANDALONE", "1")
    env.update(extra)
    env["DISPLAY"] = display
    env["GZ_IP"] = "127.0.0.1"
    return env


def _launch_gz_gui_client(
    display: str,
    *,
    attach_to_running_server: bool = False,
    artifact_directory: Path | None = None,
    session: _CaptureSession | None = None,
    force_render_engine: str | None = None,
) -> subprocess.Popen[Any] | None:
    gz = shutil.which("gz")
    if gz is None:
        return None
    env = _gz_transport_env(display, attach_to_running_server=attach_to_running_server, session=session)
    if env is None:
        return None
    extra_args, rejected_args = _validated_gz_gui_args()
    if force_render_engine in {"ogre", "ogre2"}:
        extra_args = _replace_render_engine_args(extra_args, force_render_engine)
    config_args = _resolved_demo_gui_launch_args(extra_args)
    if session is not None and config_args:
        session.demo_gui_config_path = config_args[1]
    extra_args = [*extra_args, *config_args]
    if session is not None:
        session.gz_gui_args_rejected = rejected_args
    command = [gz, "sim", "-g", *extra_args]
    log_handle: TextIO | None = None
    log_path: Path | None = None
    if artifact_directory is not None:
        artifact_directory.mkdir(parents=True, exist_ok=True)
        log_path = artifact_directory / "gz-gui.log"
        try:
            log_handle = log_path.open("wb")
        except OSError:
            log_handle = None
            log_path = None
    if session is not None:
        session.gz_gui_log_path = log_path
        session.gz_gui_log_file = log_handle
    try:
        return subprocess.Popen(
            command,
            env=env,
            stdout=log_handle or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if log_handle is not None else subprocess.DEVNULL,
        )
    except OSError:
        if log_handle is not None:
            log_handle.close()
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
